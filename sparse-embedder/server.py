"""sparse-embedder FastAPI 서비스 (:8002) — 격리 환경.

기존 embedder(:8001, dense)와 분리된 sparse 전용 마이크로서비스.
검색 시 search_rv.py 가 쿼리 텍스트를 여기로 보내 sparsevec 리터럴을 받아
하이브리드 SQL(dense 코사인 + sparse 내적 가중합)에 사용한다.

실행:
  .venv-sparse/Scripts/python -m uvicorn server:app --host 127.0.0.1 --port 8002
  (sparse-embedder 디렉토리에서 실행)
"""
from __future__ import annotations

# ⚠️ 최상단 — pyarrow load-order segfault 회피
import pyarrow  # noqa: F401

import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import sparse_model
import morpheme  # 한국어 형태소 sparse (Kiwi + TF-IDF)

DSN = os.environ.get("DB_DSN", "postgresql://app:app@localhost:5433/recommend")

app = FastAPI(title="sparse-embedder", version="0.2.0")


class EmbedSparseRequest(BaseModel):
    texts: list[str]
    batch_size: int = 16
    # 1 = 필터 없음(기본). 2 이상이면 디코딩한 토큰 길이 미만은 제외(단음절 노이즈 컷).
    min_token_chars: int = 1


@app.get("/healthz")
def healthz():
    # 모델 로드 여부는 노출하지 않음(지연 로딩). 살아있음만 보고.
    return {"ok": True, "service": "sparse-embedder",
            "model": sparse_model.MODEL_NAME, "sparse_dim": sparse_model.SPARSE_DIM}


@app.post("/embed-sparse")
def embed_sparse(req: EmbedSparseRequest):
    """텍스트 → pgvector sparsevec 리터럴 리스트.

    응답: {"sparse": ["{idx:val,...}/250002", ...], "dim": 250002}
    """
    if not req.texts:
        return {"sparse": [], "dim": sparse_model.SPARSE_DIM}
    literals = sparse_model.encode_sparsevec(
        req.texts, batch_size=req.batch_size,
        min_token_chars=max(1, int(req.min_token_chars)),
    )
    return {"sparse": literals, "dim": sparse_model.SPARSE_DIM,
            "min_token_chars": req.min_token_chars}


class SparseExplainRequest(BaseModel):
    query: str
    doc: str
    top_k: int = 30
    min_token_chars: int = 1


@app.post("/sparse-explain")
def sparse_explain(req: SparseExplainRequest):
    """쿼리·상품 텍스트의 sparse 표현을 토큰 단위로 분해.

    min_token_chars > 1 이면 디코딩 길이 미만 토큰을 양쪽에서 제외해서
    실제 검색 점수(쿼리측 필터로 동일 효과)와 일관된 분해를 보여준다.

    반환:
      - matches : 두 텍스트에 공통 등장한 토큰 + 각각 가중치 + 내적 기여도(q_w*d_w)
      - query_tokens / doc_tokens : 각 텍스트의 top-K 가중 토큰
      - inner_product : matches 기여도 합 (= sparse 내적, |sparse_ip| 와 동일)
    """
    model = sparse_model.get_model()
    out = model.encode(
        [req.query, req.doc],
        batch_size=2,
        return_dense=False, return_sparse=True, return_colbert_vecs=False,
    )
    qlw, dlw = out["lexical_weights"][0], out["lexical_weights"][1]
    tok = model.tokenizer
    min_chars = max(1, int(req.min_token_chars))

    def _decode(tid: int) -> str:
        # convert_ids_to_tokens 은 서브워드 원형(예: "▁로즈"), decode 는 자연 텍스트("로즈").
        # 둘 다 줘서 UI 가 선택 표시할 수 있게 함.
        return tok.decode([tid]).strip()

    def _passes(tid: int) -> bool:
        return min_chars <= 1 or len(_decode(tid)) >= min_chars

    def _to_list(lw: dict, top_k: int) -> list:
        items = [(int(tid), float(w)) for tid, w in lw.items()
                 if float(w) > 0 and _passes(int(tid))]
        items.sort(key=lambda x: x[1], reverse=True)
        return [{
            "token_id": tid,
            "token": tok.convert_ids_to_tokens(tid),
            "text": _decode(tid),
            "weight": round(w, 5),
        } for tid, w in items[:top_k]]

    qmap = {int(tid): float(w) for tid, w in qlw.items()
            if float(w) > 0 and _passes(int(tid))}
    dmap = {int(tid): float(w) for tid, w in dlw.items()
            if float(w) > 0 and _passes(int(tid))}
    common = sorted(set(qmap) & set(dmap), key=lambda tid: qmap[tid] * dmap[tid], reverse=True)
    matches, total = [], 0.0
    for tid in common:
        contrib = qmap[tid] * dmap[tid]
        total += contrib
        matches.append({
            "token_id": tid,
            "token": tok.convert_ids_to_tokens(tid),
            "text": _decode(tid),
            "q_weight": round(qmap[tid], 5),
            "d_weight": round(dmap[tid], 5),
            "contribution": round(contrib, 6),
        })

    return {
        "matches": matches[:req.top_k],
        "n_matches": len(matches),
        "inner_product": round(total, 5),
        "query_tokens": _to_list(qlw, req.top_k),
        "doc_tokens": _to_list(dlw, req.top_k),
    }


# ─────────────────────────────────────────────────────────────────────
# 한국어 형태소 sparse (Kiwi + TF-IDF) — BGE-M3 토크나이저 한계 보완
# ─────────────────────────────────────────────────────────────────────

class EmbedMorphemeRequest(BaseModel):
    texts: list[str]


@app.post("/embed-morpheme")
def embed_morpheme(req: EmbedMorphemeRequest):
    """텍스트 → 한국어 형태소 sparsevec 리터럴 리스트 (DB 의 morpheme_vocab 사용)."""
    if not req.texts:
        return {"sparse": [], "dim": morpheme.SPARSE_DIM, "vocab_size": 0}
    try:
        vocab, idf, _ = morpheme.get_cached_vocab(DSN)
    except Exception as e:
        raise HTTPException(503, f"morpheme_vocab 로드 실패: {e}")
    if not vocab:
        raise HTTPException(503, "morpheme_vocab 이 비어 있음 — build_morpheme_vocab.py 먼저 실행")
    morpheme.get_kiwi()  # 워밍업
    literals = [morpheme.encode_sparsevec(t, vocab, idf) for t in req.texts]
    return {"sparse": literals, "dim": morpheme.SPARSE_DIM, "vocab_size": len(vocab)}


class MorphemeExplainRequest(BaseModel):
    query: str
    doc: str
    top_k: int = 30


@app.post("/morpheme-explain")
def morpheme_explain(req: MorphemeExplainRequest):
    """쿼리·상품 텍스트의 형태소 매칭 분해 (BGE sparse 의 sparse-explain 형태소 버전).

    반환:
      - matches : 두 텍스트 공통 형태소 + 가중치 + 기여도
      - inner_product : matches 기여도 합
      - query_tokens / doc_tokens : 각 텍스트의 상위 형태소
    """
    try:
        vocab, idf, vocab_inv = morpheme.get_cached_vocab(DSN)
    except Exception as e:
        raise HTTPException(503, f"morpheme_vocab 로드 실패: {e}")
    if not vocab:
        raise HTTPException(503, "morpheme_vocab 이 비어 있음")
    morpheme.get_kiwi()

    q_sp = morpheme.encode_tfidf(req.query, vocab, idf)
    d_sp = morpheme.encode_tfidf(req.doc, vocab, idf)

    matches_tuples = morpheme.explain_match(q_sp, d_sp, vocab_inv)
    matches = [
        {"word": w, "q_weight": round(qw, 5), "d_weight": round(dw, 5),
         "contribution": round(c, 6)}
        for w, qw, dw, c in matches_tuples
    ]

    def _top(sp: dict, top_k: int) -> list:
        items = sorted(sp.items(), key=lambda x: -x[1])[:top_k]
        return [{"word": vocab_inv[idx], "weight": round(w, 5)} for idx, w in items]

    return {
        "matches": matches[:req.top_k],
        "n_matches": len(matches),
        "inner_product": round(sum(m["contribution"] for m in matches), 5),
        "query_tokens": _top(q_sp, req.top_k),
        "doc_tokens": _top(d_sp, req.top_k),
        "vocab_size": len(vocab),
    }


@app.post("/morpheme-reload-vocab")
def morpheme_reload_vocab():
    """morpheme_vocab 재구축 후 메모리 캐시 강제 리로드."""
    morpheme.invalidate_cache()
    vocab, idf, _ = morpheme.get_cached_vocab(DSN)
    return {"ok": True, "vocab_size": len(vocab)}
