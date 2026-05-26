"""한국어 형태소 기반 sparse 인코딩 — PoC.

BGE-M3 의 XLM-R 토크나이저가 한국어를 음절로 쪼개는 한계를 우회해서,
Kiwi 형태소 분석기로 단어 단위 추출 → TF-IDF sparse 벡터를 만든다.
"""
from __future__ import annotations
import math
from collections import Counter
from kiwipiepy import Kiwi

# Windows 한글 경로 회피용 — venv 가 한글 폴더 안에 있어 C++ 확장이 모델 못 찾음.
# install 시 C:\kiwi_model 로 한 번 복사해두고 그쪽을 가리킴.
KIWI_MODEL_PATH = r"C:\kiwi_model"

# 의미 단위 품사만 유지 (조사 J*, 어미 E*, 접미사 X*, 부호 S* 다수 제외)
KEEP_POS = {
    "NNG",  # 일반명사 ★
    "NNP",  # 고유명사 ★
    "VV",   # 동사 어간
    "VA",   # 형용사 어간
    "MAG",  # 일반부사
    "SL",   # 외래어 (영문 단어)
    "SN",   # 숫자
    "SH",   # 한자
    "NNB",  # 의존명사
}

# 너무 흔하고 단독으론 의미 약한 형태소 컷
STOPWORDS = {"것", "수", "등", "및", "곳", "점", "데", "때", "면", "거"}


def make_kiwi() -> Kiwi:
    return Kiwi(model_path=KIWI_MODEL_PATH)


def extract_morphemes(text: str, kiwi: Kiwi) -> list[str]:
    """텍스트 → 의미 형태소 리스트."""
    if not text:
        return []
    result = kiwi.analyze(text)
    if not result:
        return []
    tokens = result[0][0]
    return [
        t.form for t in tokens
        if t.tag in KEEP_POS and t.form not in STOPWORDS
    ]


def build_vocab(docs: list[str], kiwi: Kiwi, min_df: int = 2) -> tuple[dict, dict]:
    """문서 전체에서 사전(vocab) + 문서빈도(df) 구축.

    min_df: 이 미만 문서에 등장한 단어는 제외 (희귀어 컷).
    """
    df_counter = Counter()
    for text in docs:
        # 문서 단위 출현(set) — TF 가 아니라 DF
        morphemes = set(extract_morphemes(text, kiwi))
        for m in morphemes:
            df_counter[m] += 1
    vocab_list = sorted(m for m, c in df_counter.items() if c >= min_df)
    vocab = {m: i for i, m in enumerate(vocab_list)}
    df = {m: df_counter[m] for m in vocab}
    return vocab, df


def compute_idf(df: dict, n_docs: int) -> dict:
    """smoothed IDF: log((N+1)/(df+1)) + 1"""
    return {m: math.log((n_docs + 1) / (c + 1)) + 1 for m, c in df.items()}


def encode_tfidf(text: str, vocab: dict, idf: dict, kiwi: Kiwi) -> dict[int, float]:
    """텍스트 → {vocab_idx: tfidf_weight}."""
    morphemes = extract_morphemes(text, kiwi)
    if not morphemes:
        return {}
    tf = Counter(morphemes)
    n = len(morphemes)
    out = {}
    for m, c in tf.items():
        if m not in vocab:
            continue
        w = (c / n) * idf.get(m, 0.0)
        if w > 0:
            out[vocab[m]] = w
    return out


def inner_product(q: dict, d: dict) -> float:
    """두 sparse 표현의 내적."""
    if len(q) > len(d):
        q, d = d, q
    return sum(d.get(k, 0.0) * v for k, v in q.items())


def explain_match(q: dict, d: dict, vocab_inv: dict) -> list[tuple]:
    """매칭 단어 리스트 [(word, q_w, d_w, contribution)] — 기여도 desc."""
    matches = []
    for k, qw in q.items():
        if k in d:
            contrib = qw * d[k]
            matches.append((vocab_inv[k], qw, d[k], contrib))
    matches.sort(key=lambda x: -x[3])
    return matches
