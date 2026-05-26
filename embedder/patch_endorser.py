"""
착용자(endorser) 정보를 tags + product_descriptions에 반영 후 재임베딩.

product_name에 "하츠투하츠 유하/주은 착용" 같이 착용자가 명시된 상품에 대해:
  1) product_enriched.tags  → endorser 태그 추가
  2) product_enriched.desc_persona → 착용자 문장 prefix
  3) product_descriptions   → 위 변경 반영해 재임베딩

사용:
  python -m embedder.patch_endorser [--dry-run]
"""

import argparse, json, sys
import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

load_dotenv()

from embedder.enrich_claude import DB_DSN
from embedder.embed_descriptions import embed_one

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── 패치 대상: rv_id → (그룹, 멤버) ──────────────────────────────────
PATCHES = {
    85:  {"group": "하츠투하츠", "member": "유하"},
    113: {"group": "하츠투하츠", "member": "주은"},
}


def _endorser_tags(info: dict) -> list[dict]:
    tags = [
        {"tag": info["group"], "tag_category": "endorser", "confidence": 1.0},
    ]
    if info["member"]:
        tags.append({"tag": info["member"], "tag_category": "endorser", "confidence": 1.0})
    return tags


def _prepend_endorser(existing: str | None, info: dict) -> str:
    prefix = f"{info['group']} {info['member']} 착용 상품입니다."
    if existing:
        return f"{prefix} {existing}"
    return prefix


def process(conn, rv_id: int, info: dict, dry_run: bool) -> str:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT tags, desc_persona FROM product_enriched WHERE rv_product_id=%s",
            (rv_id,),
        )
        row = cur.fetchone()
        if not row:
            return "SKIP(no enriched row)"

        old_tags: list = row["tags"] or []
        old_persona: str | None = row["desc_persona"]

        # 이미 endorser 태그 있으면 스킵
        if any(t.get("tag_category") == "endorser" for t in old_tags):
            return "SKIP(endorser already present)"

        new_tags = old_tags + _endorser_tags(info)
        new_persona = _prepend_endorser(old_persona, info)

        if dry_run:
            print(f"  tags +{_endorser_tags(info)}")
            print(f"  desc_persona: {repr(old_persona)[:60]} → {repr(new_persona)[:80]}")
            return "DRY"

        cur.execute(
            """UPDATE product_enriched
                  SET tags = %s::jsonb,
                      desc_persona = %s
                WHERE rv_product_id = %s""",
            (json.dumps(new_tags, ensure_ascii=False), new_persona, rv_id),
        )
        conn.commit()

    # 재임베딩 (persona 설명 변경 반영)
    embed_one(conn, rv_id, "bge")
    return "OK"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = psycopg.connect(DB_DSN)
    for rv_id, info in PATCHES.items():
        result = process(conn, rv_id, info, args.dry_run)
        print(f"rv_id={rv_id} ({info['group']} {info['member']}): {result}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
