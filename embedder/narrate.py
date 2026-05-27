"""상품 추천 내러티브(롯데ON 스타일) 생성 — 신규/독립 모듈.

기존 검색 API(search_rv 등)는 일절 건드리지 않는다. chat2.html 전용으로,
프론트가 1) 기존 /search-rv-llm 으로 뽑은 결과를 2) 이 /narrate 에 다시 던지면
LLM이 (a) 질문 의도를 살린 인트로 문단과 (b) 상품별 추천 이유를 만들어 돌려준다.

LLM 호출 패턴은 search_rv._llm_parse_query 와 동일하게 Groq(OpenAI 호환) HTTP /
OpenAI SDK 를 쓰되, 여기서 자체적으로 구현해 기존 코드와 결합하지 않는다.
"""
from __future__ import annotations

import json
import os
import time
from typing import Literal, Optional

import requests
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

GROQ_BASE = os.environ.get("GROQ_BASE", "https://api.groq.com/openai/v1")
# narrate 전용 모델 — 검색(search-rv-llm)이 쓰는 70B 와 **다른 모델**을 기본값으로 둬서
# Groq 의 모델별 분리된 토큰 버킷(TPM)을 쓴다. 같은 70B 를 공유하면 검색+narrate 가
# 한 버킷을 잡아먹어 무료 티어 12k TPM 을 금방 넘겨 429 가 잦다.
# 품질을 더 원하면 NARRATE_MODEL=llama-3.3-70b-versatile 로 override.
NARRATE_MODEL = os.environ.get("NARRATE_MODEL", "llama-3.1-8b-instant")
OPENAI_CHAT_MODEL = os.environ.get("OPENAI_CHAT_MODEL", "gpt-4o-mini")

# 토큰 폭주 방지 — 상품 수/설명 길이 상한 (8B 6k TPM 에 한 번에 들어오도록 보수적).
MAX_ITEMS = 10
MAX_DESC_CHARS = 240

_SYSTEM_PROMPT = """\
당신은 한국 패션·라이프스타일 편집샵의 친절하고 센스 있는 매장 점원입니다.
손님의 질문(상황·분위기·예산 등)과, 우리가 골라온 상품 목록이 주어집니다.
실제 매장에서 손님 곁에서 권해주듯, 따뜻하고 다정하게 한국어로 작성하세요.

1) intro: 손님의 질문 의도와 분위기에 공감하며 받아주는 소개 문단 (2~3문장).
   - "~를 찾고 계셨죠?", "그런 분께 딱 맞는 걸로 골라봤어요" 처럼 점원이 말 걸듯 자연스럽게.
   - 곧 추천 상품을 보여준다는 흐름으로 끝맺기. 과장·허위는 금지.
2) 각 상품마다 reason: 이 상품이 왜 손님 상황에 잘 맞는지 구체적으로 (2~3문장).
   - 반드시 제공된 '상품 설명'에 근거하여, 소재·핏/실루엣·색감·디테일·계절감·
     활용 상황(코디/TPO) 중 실제로 해당하는 점을 구체적으로 짚어 설명할 것.
   - "이런 점이 좋아요", "~하실 때 특히 잘 어울려요" 처럼 권하는 말투로.
   - 없는 기능·소재를 지어내지 말 것. 가격/할인은 자연스러울 때만 가볍게.
   - 모든 상품에 대해 빠짐없이 작성.

반드시 아래 JSON 형식만 출력 (다른 텍스트 금지):
{"intro": "...", "items": [{"rv_product_id": <정수 그대로>, "reason": "..."}]}
rv_product_id 는 입력으로 받은 값을 그대로 사용하세요.

문체 규칙: 자연스러운 한국어로만 쓰고, 한자·중국어·영어 표현을 섞지 마세요
(브랜드·상품명 고유어는 예외). 정중하고 다정한 '~요' 체 중심."""


class NarrateItem(BaseModel):
    rv_product_id: Optional[int] = None
    product_name: Optional[str] = None
    brand: Optional[str] = None
    category: Optional[str] = None
    price: Optional[float] = None
    sale_price: Optional[float] = None
    description: Optional[str] = None


class NarrateReq(BaseModel):
    query: str
    items: list[NarrateItem] = []
    # 기본 OpenAI(gpt-4o-mini). 호출 실패 시 narrate() 가 다른 provider 로 1회 폴백.
    llm_provider: Literal["groq", "openai"] = "openai"


def _build_user_content(query: str, items: list[NarrateItem]) -> str:
    """LLM user 메시지 — 질문 + 상품 목록(JSON)."""
    compact = []
    for it in items[:MAX_ITEMS]:
        desc = (it.description or "").strip()
        if len(desc) > MAX_DESC_CHARS:
            desc = desc[:MAX_DESC_CHARS] + "…"
        compact.append({
            "rv_product_id": it.rv_product_id,
            "product_name": it.product_name or "",
            "brand": it.brand or "",
            "category": it.category or "",
            "price": it.price,
            "sale_price": it.sale_price,
            "description": desc,
        })
    payload = {"user_query": query, "products": compact}
    return (
        "사용자 질문과 추천 상품 목록입니다. intro 와 각 상품 reason 을 작성하세요.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def _call_llm(provider: str, user_content: str) -> tuple[str, dict]:
    """(content, usage) 반환. 실패 시 예외."""
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    if provider == "openai":
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY 미설정")
        from openai import OpenAI
        client = OpenAI()
        r = client.chat.completions.create(
            model=OPENAI_CHAT_MODEL,
            messages=messages,
            temperature=0.6,
            response_format={"type": "json_object"},
            max_tokens=2200,
        )
        usage = r.usage
        in_tok = usage.prompt_tokens if usage else 0
        out_tok = usage.completion_tokens if usage else 0
        cost = in_tok * 0.00000015 + out_tok * 0.0000006
        return r.choices[0].message.content, {
            "input_tokens": in_tok, "output_tokens": out_tok, "cost_usd": round(cost, 8),
        }
    # groq (OpenAI 호환 HTTP)
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY 미설정")
    body = {
        "model": NARRATE_MODEL,
        "messages": messages,
        "temperature": 0.6,
        "response_format": {"type": "json_object"},
        "max_tokens": 2200,
    }
    # 무료 티어 429(TPM 초과) 대응. Groq 토큰 버킷은 연속 충전(reset≈수백 ms~수초)되므로
    # 429 면 Retry-After/기본 백오프만큼 기다렸다 재시도한다(총 3회, 누적 ≤ ~9s).
    attempts = 3
    resp = None
    for i in range(attempts):
        resp = requests.post(
            f"{GROQ_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=body,
            timeout=30,
        )
        if resp.status_code == 429 and i < attempts - 1:
            wait = 3.0 * (i + 1)  # 3s, 6s
            ra = resp.headers.get("retry-after")
            if ra:
                try:
                    wait = min(8.0, float(ra))
                except (TypeError, ValueError):
                    pass
            time.sleep(wait)
            continue
        break
    resp.raise_for_status()
    rj = resp.json()
    usage = rj.get("usage", {})
    in_tok = usage.get("prompt_tokens", 0)
    out_tok = usage.get("completion_tokens", 0)
    cost = in_tok * 0.00000059 + out_tok * 0.00000079
    return rj["choices"][0]["message"]["content"], {
        "input_tokens": in_tok, "output_tokens": out_tok, "cost_usd": round(cost, 8),
    }


@router.post("/narrate")
def narrate(req: NarrateReq):
    """검색 결과를 받아 롯데ON 스타일 인트로 + 상품별 추천 이유를 생성.

    LLM 실패 시에도 200 으로 graceful 응답(intro 빈 문자열, reasons 없음) —
    프론트가 카드만이라도 정상 렌더하도록.
    """
    if not req.items:
        return {"intro": "", "items": [], "provider": req.llm_provider,
                "note": "상품 없음", "_usage": {}}

    user_content = _build_user_content(req.query, req.items)

    # narration 은 무조건 요청 provider(기본 gpt-4o-mini)로만. 다른 모델로 갈아타지 않고
    # 일시 오류 시 동일 provider 로 1회 재시도만 한다.
    prov = req.llm_provider
    last_err = None
    for _attempt in range(2):
        try:
            content, usage = _call_llm(prov, user_content)
            data = json.loads(content)
            intro = (data.get("intro") or "").strip()
            reasons: dict[int, str] = {}
            for r in (data.get("items") or []):
                pid = r.get("rv_product_id")
                reason = (r.get("reason") or "").strip()
                if pid is not None and reason:
                    try:
                        reasons[int(pid)] = reason
                    except (TypeError, ValueError):
                        pass
            # 응답 items 는 입력 순서를 보존해 돌려준다(매칭 누락 대비).
            out_items = [
                {"rv_product_id": it.rv_product_id,
                 "reason": reasons.get(it.rv_product_id, "")}
                for it in req.items[:MAX_ITEMS]
            ]
            return {
                "intro": intro,
                "items": out_items,
                "provider": prov,
                "note": None if prov == req.llm_provider else f"{req.llm_provider} 실패 → {prov} 폴백",
                "_usage": usage,
            }
        except Exception as e:
            last_err = e
            continue

    # 모든 provider 실패 — graceful (카드만 렌더되도록 200).
    return {
        "intro": "",
        "items": [{"rv_product_id": it.rv_product_id, "reason": ""}
                  for it in req.items[:MAX_ITEMS]],
        "provider": req.llm_provider,
        "note": f"narrate 실패: {last_err}",
        "_usage": {},
    }
