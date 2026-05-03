"""LLM 두 군데서 사용:
   1) parse_query: 자연어 → 구조화 필터 JSON
   2) rerank_and_explain: 후보 → 추천 이유 + rerank
"""
import json
import os
from typing import Any

from anthropic import Anthropic

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

_client: Anthropic | None = None


def client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic()
    return _client


PARSE_SYSTEM = """당신은 의류 쇼핑몰 검색 쿼리를 구조화 JSON으로 변환합니다.

가능한 카테고리: 상의, 하의, 아우터, 원피스, 신발, 가방, 액세서리
가능한 성별: 여성, 남성, 공용
가능한 시즌: 봄, 여름, 가을, 겨울, 사계절
가능한 스타일: 캐주얼, 미니멀, 스트릿, 클래식, 빈티지, 페미닌, 스포티, 데일리, 오피스, 러블리, 모던, 보헤미안

반드시 다음 JSON 스키마로만 응답:
{
  "filters": {
    "category": string|null,
    "gender": string|null,
    "season": string|null,
    "style": string|null,
    "color": string|null,
    "price_min": number|null,
    "price_max": number|null,
    "in_stock_only": boolean
  },
  "semantic_query": "벡터 검색용 핵심 의미 (한국어 자연어 그대로)"
}

명시되지 않은 필드는 null. 재고 언급 없으면 in_stock_only=true."""


def parse_query(user_text: str) -> dict[str, Any]:
    msg = client().messages.create(
        model=CLAUDE_MODEL,
        max_tokens=512,
        system=PARSE_SYSTEM,
        messages=[{"role": "user", "content": user_text}],
    )
    text = msg.content[0].text.strip()
    # ```json 펜스 제거
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip().rstrip("`").strip()
    return json.loads(text)


RERANK_SYSTEM = """당신은 패션 큐레이터입니다. 사용자 요구와 후보 상품 목록을 보고:
1) 가장 적합한 5개를 골라 순위 매기고
2) 각각 한 줄로 추천 이유를 작성하세요.

JSON으로만 응답:
{"recommendations": [{"product_id": int, "reason": "..."}]}
"""


def rerank_and_explain(user_text: str, candidates: list[dict]) -> dict:
    payload = {
        "user_query": user_text,
        "candidates": candidates,
    }
    msg = client().messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1024,
        system=RERANK_SYSTEM,
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
    )
    text = msg.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip().rstrip("`").strip()
    return json.loads(text)
