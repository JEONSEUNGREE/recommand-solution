"""FastAPI: 자연어 추천 API.

전체 파이프라인:
  POST /recommend
    1) LLM이 자연어 → {filters, semantic_query} JSON
    2) 임베딩 모델이 semantic_query → 벡터
    3) 앱 코드가 SQL (필터 + ORDER BY embedding <=> vec) 실행
    4) LLM이 후보 → rerank + 추천 이유
"""
from fastapi import FastAPI
from pydantic import BaseModel

from . import llm
from .search import search

app = FastAPI(title="Recommend MVP")


class RecommendRequest(BaseModel):
    query: str
    top_candidates: int = 30


@app.post("/recommend")
def recommend(req: RecommendRequest):
    parsed = llm.parse_query(req.query)
    candidates = search(parsed["filters"], parsed["semantic_query"], k=req.top_candidates)

    # rerank용으로 LLM에 보낼 가벼운 페이로드
    light = [
        {
            "product_id": c["id"], "name": c["name"], "category": c["category"],
            "subcategory": c["subcategory"], "gender": c["gender"], "season": c["season"],
            "style": c["style"], "color": c["color"],
            "price": c["sale_price"] or c["price"], "stock": c["stock"],
        }
        for c in candidates
    ]
    final = llm.rerank_and_explain(req.query, light) if candidates else {"recommendations": []}

    return {
        "parsed": parsed,
        "candidate_count": len(candidates),
        "recommendations": final["recommendations"],
        "candidates_preview": candidates[:5],
    }


@app.get("/healthz")
def health():
    return {"ok": True}
