"""한국어 형태소 sparse 인코더 — 운영 모듈 (PoC 에서 승격).

DB 의 morpheme_vocab 테이블을 사전(vocab)으로 사용하며, 백필·쿼리 양쪽에서
같은 vocab/IDF 를 공유한다. 사전은 일회 구축 → 메모리 캐시.

흐름:
   build_morpheme_vocab.py 가 morpheme_vocab 테이블을 채움 (1회)
        ↓
   backfill_morpheme.py 가 embedding_morpheme 컬럼을 채움 (1회)
        ↓
   server.py /embed-morpheme 가 검색 시 쿼리 인코딩 (메모리 캐시 사용)
"""
from __future__ import annotations
import math, os, threading
from collections import Counter
from kiwipiepy import Kiwi

# Windows 한글 경로 회피용 — 한글 경로에선 Kiwi 의 C++ 확장이 모델 못 찾음.
# build_morpheme_vocab.py 실행 전 모델을 한 번 복사해두기.
KIWI_MODEL_PATH = os.environ.get("KIWI_MODEL_PATH", r"C:\kiwi_model")

# 의미 단위 품사만 유지 (조사·어미·접미사 제외)
KEEP_POS = {
    "NNG",  # 일반명사
    "NNP",  # 고유명사
    "VV",   # 동사 어간
    "VA",   # 형용사 어간
    "MAG",  # 일반부사
    "SL",   # 외래어 (영문)
    "SN",   # 숫자
    "SH",   # 한자
    "NNB",  # 의존명사
}

# 너무 흔하고 단독 의미 약한 형태소 컷
STOPWORDS = {"것", "수", "등", "및", "곳", "점", "데", "때", "면", "거"}

# pgvector sparsevec 차원 (migration 019 와 일치)
SPARSE_DIM = 65536

_kiwi = None
_kiwi_lock = threading.Lock()


def get_kiwi() -> Kiwi:
    """Kiwi 싱글톤 — 모델 1회 로드(~1.3초)."""
    global _kiwi
    if _kiwi is None:
        with _kiwi_lock:
            if _kiwi is None:
                _kiwi = Kiwi(model_path=KIWI_MODEL_PATH)
    return _kiwi


def extract_morphemes(text: str) -> list[str]:
    """텍스트 → 의미 형태소 리스트 (조사·어미·stopword 제외)."""
    if not text:
        return []
    result = get_kiwi().analyze(text)
    if not result:
        return []
    return [
        t.form for t in result[0][0]
        if t.tag in KEEP_POS and t.form not in STOPWORDS
    ]


def build_vocab(docs: list[str], min_df: int = 3) -> tuple[dict, dict]:
    """문서 모음에서 vocab(단어→idx) + df(단어→문서빈도) 구축.

    idx 는 1-based (pgvector sparsevec 호환).
    min_df 미만 단어는 제외 → 희귀어 노이즈 컷.
    """
    df_counter = Counter()
    for text in docs:
        morphemes = set(extract_morphemes(text))
        for m in morphemes:
            df_counter[m] += 1
    keep = sorted(m for m, c in df_counter.items() if c >= min_df)
    vocab = {m: i + 1 for i, m in enumerate(keep)}  # 1-based
    df = {m: df_counter[m] for m in vocab}
    return vocab, df


def compute_idf(df: dict, n_docs: int) -> dict:
    """smoothed IDF: log((N+1)/(df+1)) + 1"""
    return {m: math.log((n_docs + 1) / (c + 1)) + 1 for m, c in df.items()}


def encode_tfidf(text: str, vocab: dict, idf: dict) -> dict[int, float]:
    """텍스트 → {vocab_idx: tfidf_weight}. OOV 단어는 제외(=0)."""
    morphemes = extract_morphemes(text)
    if not morphemes:
        return {}
    tf = Counter(morphemes)
    n = len(morphemes)
    out = {}
    for m, c in tf.items():
        idx = vocab.get(m)
        if idx is None:
            continue
        w = (c / n) * idf.get(m, 0.0)
        if w > 0:
            out[idx] = w
    return out


def to_sparsevec_literal(weights: dict[int, float]) -> str:
    """{idx: weight} → pgvector sparsevec 리터럴 '{idx:val,...}/dim'.

    빈 경우 폴백 '{1:0}/dim' (pgvector 가 빈 sparsevec 거부하므로).
    """
    if not weights:
        return "{1:0}/%d" % SPARSE_DIM
    items = sorted(weights.items())
    body = ",".join(f"{idx}:{w:.6f}" for idx, w in items)
    return "{%s}/%d" % (body, SPARSE_DIM)


def encode_sparsevec(text: str, vocab: dict, idf: dict) -> str:
    """텍스트 → sparsevec 리터럴 (DB/검색 바로 사용)."""
    return to_sparsevec_literal(encode_tfidf(text, vocab, idf))


def inner_product(q: dict, d: dict) -> float:
    """두 sparse 표현의 내적."""
    if len(q) > len(d):
        q, d = d, q
    return sum(d.get(k, 0.0) * v for k, v in q.items())


def explain_match(q: dict, d: dict, vocab_inv: dict) -> list[tuple]:
    """매칭 단어 [(word, q_w, d_w, contribution)] — 기여도 desc."""
    matches = []
    for k, qw in q.items():
        if k in d:
            contrib = qw * d[k]
            matches.append((vocab_inv[k], qw, d[k], contrib))
    matches.sort(key=lambda x: -x[3])
    return matches


# ──────────────────────────────────────────────────────────────
# DB 캐시 (server.py 에서 사용)
# ──────────────────────────────────────────────────────────────
_vocab_cache: dict | None = None
_idf_cache: dict | None = None
_vocab_inv_cache: dict | None = None
_cache_lock = threading.Lock()


def load_vocab_from_db(conn) -> tuple[dict, dict, dict]:
    """morpheme_vocab 전체 로드. (vocab, idf, vocab_inv)"""
    with conn.cursor() as cur:
        cur.execute("SELECT idx, morpheme, idf FROM morpheme_vocab")
        rows = cur.fetchall()
    vocab = {r[1]: r[0] for r in rows}
    idf = {r[1]: r[2] for r in rows}
    vocab_inv = {r[0]: r[1] for r in rows}
    return vocab, idf, vocab_inv


def get_cached_vocab(dsn: str) -> tuple[dict, dict, dict]:
    """DB 에서 한 번만 vocab 로드, 이후 메모리 캐시."""
    global _vocab_cache, _idf_cache, _vocab_inv_cache
    if _vocab_cache is None:
        with _cache_lock:
            if _vocab_cache is None:
                import psycopg
                conn = psycopg.connect(dsn)
                try:
                    v, i, inv = load_vocab_from_db(conn)
                finally:
                    conn.close()
                _vocab_cache, _idf_cache, _vocab_inv_cache = v, i, inv
    return _vocab_cache, _idf_cache, _vocab_inv_cache


def invalidate_cache() -> None:
    """vocab 재구축 후 강제 리로드용."""
    global _vocab_cache, _idf_cache, _vocab_inv_cache
    with _cache_lock:
        _vocab_cache = _idf_cache = _vocab_inv_cache = None
