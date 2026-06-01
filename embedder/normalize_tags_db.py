"""
기존 product_enriched.tags 데이터 일괄 정규화.

정규화 규칙:
  - 태그 문자열 앞뒤 공백 제거
  - 내부 공백 제거 (큐빅 지르코니아 → 큐빅지르코니아)
  - 동일 정규화 결과인 중복 태그 제거 (먼저 나온 것 유지)

사용:
  python -m embedder.normalize_tags_db [--dry-run]
"""

import argparse
import json
import os

import psycopg2
from psycopg2.extras import RealDictCursor

from .tag_normalizer import normalize_tags


DB_DSN = os.environ.get("DB_DSN", "postgresql://app:app@localhost:5433/recommend")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="변경 없이 대상 건수만 출력")
    args = ap.parse_args()

    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # 공백이 포함된 태그가 있는 상품만 조회
    cur.execute(r"""
        SELECT rv_product_id, tags
        FROM product_enriched
        WHERE tags IS NOT NULL
          AND tags != 'null'::jsonb
          AND jsonb_array_length(tags) > 0
          AND tags::text ~ '\s'
    """)
    rows = cur.fetchall()
    print(f"공백 포함 태그 상품: {len(rows)}건")

    changed = 0
    for row in rows:
        original = row["tags"]  # already parsed by psycopg2 with jsonb
        if isinstance(original, str):
            original = json.loads(original)
        normalized = normalize_tags(original)
        if normalized != original:
            changed += 1
            if not args.dry_run:
                cur2 = conn.cursor()
                cur2.execute(
                    "UPDATE product_enriched SET tags = %s WHERE rv_product_id = %s",
                    (json.dumps(normalized, ensure_ascii=False), row["rv_product_id"]),
                )
                cur2.close()

    print(f"정규화 대상: {changed}건" + (" (dry-run, 실제 변경 없음)" if args.dry_run else " → 업데이트 완료"))

    if not args.dry_run:
        conn.commit()
        print("커밋 완료")
    conn.close()


if __name__ == "__main__":
    main()
