"""
Step 2 · 검증 (validate_claude.py) — Skeleton

PDF v1.0 §5.프롬프트4 (검증 + 트랩 회피) 기반.
1차 추출 결과(product_enriched.raw_response)를 가벼운 모델(Haiku)로 다시 검증:
  - 환각 검사 (원본에 없는 정보가 추출됐는지)
  - 누락 검사 (명시적 정보가 빠졌는지)
  - 트랩 검사 (이미지 트랩 / 호환 상품 오인)
  - 형식 검사 (단위·한국어 일관성)

결과를 product_enriched의 validation_* 컬럼에 갱신. enrich_status='validated'로 전환.

사용:
  python -m embedder.validate_claude <rv_product_id>
  python -m embedder.validate_claude --batch --status extracted --limit 50
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import psycopg

# 공통 유틸 재사용
from embedder.enrich_claude import DB_DSN, CLAUDE_BIN  # noqa


VALIDATE_SCHEMA = {
    "type": "object",
    "required": ["is_valid", "trust_score"],
    "properties": {
        "is_valid": {"type": "boolean"},
        "trust_score": {"type": "number", "minimum": 0, "maximum": 1},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "errors": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["field", "issue"],
                "properties": {
                    "field": {"type": "string"},
                    "issue": {"type": "string"},
                    "suggested_fix": {"type": "string"},
                },
            },
        },
        "trap_detected": {
            "type": "object",
            "properties": {
                "is_trap": {"type": "boolean"},
                "trap_type": {"type": "string"},
                "explanation": {"type": "string"},
            },
        },
    },
}


SYSTEM_PROMPT = """당신은 상품 데이터 추출 결과를 검증하는 QA 담당입니다.
(참조: Stage 2 LLM 추출 전략 v1.0 · 프롬프트 4)

[검증 항목]
1. 환각 검사: 원본(텍스트+이미지)에 없는 정보가 추출됐는지
2. 누락 검사: 명시적 정보가 빠졌는지
3. 트랩 검사: 이미지가 본 상품을 정확히 표현하는지
   - 마케팅 이미지인데 본체 정보로 잡았는지
   - 호환/추천 상품을 본 상품으로 오인했는지
4. 형식 검사: JSON 형식 / 단위 / 한국어 일관성

[규칙]
- 의심 가는 부분만 보고
- 확실한 오류는 errors[].suggested_fix에 정정안 제시
- 의심스럽지만 확실하지 않으면 warnings

[출력] JSON만. 코드펜스 없이.
"""


def call_claude_validate(payload: str) -> tuple[dict, dict]:
    """Haiku로 검증. 가벼운 모델 사용."""
    model = os.environ.get("VALIDATE_MODEL", "haiku")
    cmd = f'{CLAUDE_BIN} -p --model {model} --output-format json'
    res = subprocess.run(
        cmd, input=payload, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False, shell=True,
    )
    if res.returncode != 0:
        raise RuntimeError(f"claude validate failed (exit {res.returncode}): {(res.stderr or '')[:500]}")
    envelope = json.loads(res.stdout)
    raw = envelope.get("result") or ""
    text = raw.strip().lstrip("`").rstrip("`")
    if text.startswith("json\n"):
        text = text[5:]
    data = json.loads(text)
    return data, {
        "model": envelope.get("model"),
        "input_tokens": envelope.get("usage", {}).get("input_tokens"),
        "output_tokens": envelope.get("usage", {}).get("output_tokens"),
    }


def fetch_enriched(conn, rv_product_id: int) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT pe.rv_product_id, pe.advertiser_id, pe.product_code,
                      pe.category, pe.brand, pe.category_attributes, pe.tags,
                      pe.desc_situation, pe.desc_material, pe.desc_style, pe.desc_persona,
                      pe.image_types, pe.raw_response,
                      rv.product_name, rv.html_path, rv.image_dir
                 FROM product_enriched pe
                 JOIN rv_products rv ON rv.id = pe.rv_product_id
                WHERE pe.rv_product_id = %s""",
            (rv_product_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = ["rv_product_id","advertiser_id","product_code","category","brand",
                "category_attributes","tags","desc_situation","desc_material","desc_style","desc_persona",
                "image_types","raw_response","product_name","html_path","image_dir"]
        return dict(zip(cols, row))


def build_validate_payload(row: dict) -> str:
    """원본(텍스트+이미지 경로) + 추출 결과를 함께 제시."""
    # 원본 텍스트는 extracted.json에서
    extracted_path = Path(row["image_dir"]).parent / "extracted.json"
    page_text = ""
    if extracted_path.exists():
        with open(extracted_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        page_text = meta.get("product_name_extracted") or meta.get("body_text") or ""

    extraction = {
        "product_name": row["product_name"],
        "category": row["category"],
        "brand": row["brand"],
        "category_attributes": row["category_attributes"],
        "tags": row["tags"],
        "descriptions": {
            "situation": row["desc_situation"],
            "material": row["desc_material"],
            "style": row["desc_style"],
            "persona": row["desc_persona"],
        },
        "image_types": row["image_types"],
    }

    schema_str = json.dumps(VALIDATE_SCHEMA, ensure_ascii=False, indent=2)
    return (
        f"{SYSTEM_PROMPT}\n\n"
        f"=== 반드시 따라야 할 JSON 스키마 ===\n{schema_str}\n\n"
        f"=== 원본 페이지 텍스트 ===\n{page_text[:4000]}\n\n"
        f"=== 1차 추출 결과 ===\n{json.dumps(extraction, ensure_ascii=False, indent=2)}\n\n"
        f"=== 검증 출력 ===\nJSON 한 덩어리만."
    )


def upsert_validation(conn, rv_product_id: int, result: dict, meta: dict):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE product_enriched SET
              validation_score    = %s,
              validation_warnings = %s::jsonb,
              validation_errors   = %s::jsonb,
              trap_detected       = %s::jsonb,
              validated_at        = NOW(),
              validated_by        = %s,
              enrich_status       = CASE WHEN %s THEN 'validated' ELSE 'failed' END
             WHERE rv_product_id = %s
            """,
            (
                result.get("trust_score"),
                json.dumps(result.get("warnings") or [], ensure_ascii=False),
                json.dumps(result.get("errors") or [], ensure_ascii=False),
                json.dumps(result.get("trap_detected") or {}, ensure_ascii=False),
                meta.get("model") or "haiku",
                bool(result.get("is_valid", True)),
                rv_product_id,
            ),
        )
    conn.commit()


def validate_one(conn, rv_product_id: int) -> dict:
    row = fetch_enriched(conn, rv_product_id)
    if not row:
        raise RuntimeError(f"product_enriched row not found: rv_product_id={rv_product_id}")
    payload = build_validate_payload(row)
    print(f"[validate] {row['product_code']} → calling claude (haiku)...", file=sys.stderr)
    result, meta = call_claude_validate(payload)
    upsert_validation(conn, rv_product_id, result, meta)
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("rv_product_id", type=int, nargs="?")
    ap.add_argument("--batch", action="store_true", help="여러 상품 일괄 검증")
    ap.add_argument("--status", default="extracted", help="배치 시 대상 status (extracted)")
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args()

    conn = psycopg.connect(DB_DSN)

    if args.batch:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT rv_product_id FROM product_enriched WHERE enrich_status=%s LIMIT %s",
                (args.status, args.limit),
            )
            ids = [r[0] for r in cur.fetchall()]
        print(f"[validate] batch: {len(ids)} rows", file=sys.stderr)
        for rv_id in ids:
            try:
                r = validate_one(conn, rv_id)
                print(f"  {rv_id} → trust={r.get('trust_score')} valid={r.get('is_valid')}", file=sys.stderr)
            except Exception as e:
                print(f"  {rv_id} ERR: {e}", file=sys.stderr)
    else:
        if not args.rv_product_id:
            ap.error("rv_product_id required (또는 --batch)")
        result = validate_one(conn, args.rv_product_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
