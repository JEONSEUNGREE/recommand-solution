"""
오염 상품 DB 직접 정리 + 재임베딩.

Claude 재호출 없이:
  1) product_enriched.desc_* 컬럼에서 연예인/셀럽 포함 문장 제거
  2) product_enriched.tags JSONB에서 해당 태그 제거
  3) product_descriptions 벡터 재생성 (bge)

사용:
  python -m embedder.clean_contaminated [--dry-run] [--skip-ids 59]
"""

import argparse
import re
import sys
from datetime import datetime

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

_CONTAMINATION_RE = re.compile(
    r"(연예인|셀럽|셀러브리티|아이돌|협찬|드라마\s*출연|방송\s*출연|스타가|스타의)",
    re.IGNORECASE,
)

# 오염된 문장: 마침표/쉼표 기준으로 나눠 오염 포함 구간만 제거
_SENT_SPLIT = re.compile(r"(?<=[\.!?。，,])\s*")


def _clean_text(text: str | None) -> str | None:
    """오염 패턴이 포함된 문장 단위 제거. 남은 텍스트가 없으면 None."""
    if not text:
        return text
    if not _CONTAMINATION_RE.search(text):
        return text  # 오염 없음 — 그대로

    # 문장 단위로 분리해 오염 문장만 제거
    sentences = _SENT_SPLIT.split(text)
    clean = [s for s in sentences if s.strip() and not _CONTAMINATION_RE.search(s)]
    result = " ".join(clean).strip()
    return result if result else None


def _clean_tags(tags) -> list:
    """tags JSONB 배열에서 오염 항목 제거. 키는 'tag' 또는 'name' 모두 지원."""
    if not tags:
        return tags
    return [
        t for t in tags
        if not _CONTAMINATION_RE.search(
            t.get("tag", t.get("name", "")) + " " + t.get("value", "")
        )
    ]


def fetch_contaminated_ids(conn, skip_ids: set) -> list[int]:
    patterns = [
        "%연예인%", "%셀럽%", "%셀러브리티%", "%아이돌%",
        "%협찬%", "%드라마 출연%", "%방송 출연%", "%스타가%", "%스타의%",
    ]
    like_clauses = " OR ".join(
        f"pe.desc_situation ILIKE '{p}' OR pe.desc_material ILIKE '{p}' "
        f"OR pe.desc_style ILIKE '{p}' OR pe.desc_persona ILIKE '{p}' "
        f"OR pe.tags::text ILIKE '{p}'"
        for p in patterns
    )
    pd_clauses = " OR ".join(f"pd.description ILIKE '{p}'" for p in patterns)
    sql = f"""
        SELECT DISTINCT COALESCE(pe.rv_product_id, pd.rv_product_id)
          FROM product_enriched pe
          FULL JOIN product_descriptions pd ON pd.rv_product_id = pe.rv_product_id
         WHERE {like_clauses} OR {pd_clauses}
         ORDER BY 1
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        return [r[0] for r in cur.fetchall() if r[0] not in skip_ids]


def _log(msg: str) -> None:
    try:
        print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)
    except Exception:
        pass


def process_one(conn, rv_id: int, dry_run: bool) -> str:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT desc_situation, desc_material, desc_style, desc_persona, tags
                 FROM product_enriched WHERE rv_product_id = %s""",
            (rv_id,),
        )
        row = cur.fetchone()
        if not row:
            return "SKIP(no enriched row)"

        new_sit = _clean_text(row["desc_situation"])
        new_mat = _clean_text(row["desc_material"])
        new_sty = _clean_text(row["desc_style"])
        new_per = _clean_text(row["desc_persona"])
        new_tags = _clean_tags(row["tags"])

        changed = (
            new_sit != row["desc_situation"] or new_mat != row["desc_material"] or
            new_sty != row["desc_style"] or new_per != row["desc_persona"] or
            new_tags != row["tags"]
        )
        if dry_run:
            diffs = []
            for label, old, new in [("sit", row["desc_situation"], new_sit),
                                    ("mat", row["desc_material"], new_mat),
                                    ("sty", row["desc_style"], new_sty),
                                    ("per", row["desc_persona"], new_per)]:
                if old != new:
                    diffs.append(f"  {label}: {repr(old)[:60]} → {repr(new)[:60]}")
            note = f"DRY({'changed' if changed else 'no-change,embed-only'})"
            return note + ("\n" + "\n".join(diffs) if diffs else "")

        import json
        if changed:
            cur.execute(
                """UPDATE product_enriched
                      SET desc_situation = %s, desc_material = %s,
                          desc_style = %s, desc_persona = %s,
                          tags = %s::jsonb
                    WHERE rv_product_id = %s""",
                (new_sit, new_mat, new_sty, new_per,
                 json.dumps(new_tags, ensure_ascii=False), rv_id),
            )
            conn.commit()
    conn.commit()

    # 벡터 재생성 (product_enriched 변경 여부와 무관하게 항상)
    embed_one(conn, rv_id, "bge")
    return "OK" + ("" if changed else "(embed-only)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-ids", nargs="*", type=int, default=[],
                    help="이미 처리 완료된 rv_id (건너뜀)")
    args = ap.parse_args()

    skip = set(args.skip_ids)
    conn = psycopg.connect(DB_DSN)

    ids = fetch_contaminated_ids(conn, skip)
    _log(f"대상 {len(ids)}건 (skip {len(skip)}건)")

    ok = fail = skip_cnt = 0
    for rv_id in ids:
        try:
            result = process_one(conn, rv_id, args.dry_run)
            if result.startswith("SKIP"):
                skip_cnt += 1
                _log(f"SKIP rv_id={rv_id} ({result})")
            else:
                ok += 1
                _log(f"OK   rv_id={rv_id}  ({ok+fail}/{len(ids)})")
                if args.dry_run:
                    print(result)
        except Exception as e:
            fail += 1
            _log(f"FAIL rv_id={rv_id}: {e}")

    _log(f"완료 — OK={ok} SKIP={skip_cnt} FAIL={fail}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
