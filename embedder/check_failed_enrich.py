"""피핀 광고주 enrich 실패 후보 식별."""
import sys
sys.stdout.reconfigure(encoding='utf-8')

import psycopg2
import os
from psycopg2.extras import RealDictCursor

DB_DSN = os.environ.get("DB_DSN", "postgresql://app:app@localhost:5433/recommend")
ADV_ID = 4

conn = psycopg2.connect(DB_DSN)
cur = conn.cursor(cursor_factory=RealDictCursor)

# 1) rv_products 컬럼 확인
cur.execute("""SELECT column_name FROM information_schema.columns
               WHERE table_name='rv_products' ORDER BY ordinal_position""")
cols = [r['column_name'] for r in cur.fetchall()]
print(f"rv_products 컬럼: {cols}\n")

# 2) rv_products 전체 통계
cur.execute("""
    SELECT
      COUNT(*)                                                     AS total,
      COUNT(*) FILTER (WHERE scrape_status = 'scraped')            AS scraped,
      COUNT(*) FILTER (WHERE scrape_status IS NOT NULL AND scrape_status != 'scraped') AS scrape_other,
      COUNT(*) FILTER (WHERE scrape_status IS NULL)                AS scrape_null,
      COUNT(*) FILTER (WHERE enrich_method IS NOT NULL)            AS enrich_attempted
    FROM rv_products WHERE advertiser_id = %s
""", (ADV_ID,))
r = cur.fetchone()
print(f"rv_products (adv=4): total={r['total']}  scraped={r['scraped']}  scrape_other={r['scrape_other']}  scrape_null={r['scrape_null']}  enrich_attempted={r['enrich_attempted']}\n")

# 3) scraped 됐지만 product_enriched 에 'embedded' 가 아닌 것들
cur.execute("""
    SELECT rp.id AS rv_id, rp.product_code, rp.product_name,
           rp.scrape_status, rp.enrich_method,
           pe.enrich_status,
           rp.scraped_at, rp.enriched_at
      FROM rv_products rp
      LEFT JOIN product_enriched pe ON pe.rv_product_id = rp.id
     WHERE rp.advertiser_id = %s
       AND rp.scrape_status = 'scraped'
       AND (pe.enrich_status IS NULL OR pe.enrich_status NOT IN ('embedded'))
     ORDER BY rp.id
""", (ADV_ID,))
rows = cur.fetchall()
print(f"=== scraped 됐지만 embedded 아님 ({len(rows)}건) ===")
for r in rows[:40]:
    sa = str(r['scraped_at'])[:19] if r['scraped_at'] else '—'
    ea = str(r['enriched_at'])[:19] if r['enriched_at'] else '—'
    pname = (r['product_name'] or '')[:35]
    print(f"  rv={r['rv_id']:6d}  status={r['enrich_status'] or 'NULL':10s}  em={r['enrich_method'] or 'NULL':10s}  scraped={sa}  attempted={ea}  {pname}")
print()

# 4) 14356 단건 상세
print(f"=== rv_id=14356 상세 ===")
cur.execute("""SELECT * FROM rv_products WHERE id = 14356""")
r = cur.fetchone()
if r:
    for k, v in r.items():
        s = str(v)[:100]
        print(f"  {k:25s} = {s}")
else:
    print("  없음")
print()

cur.execute("""SELECT rv_product_id, advertiser_id, enrich_status, enrich_error,
                      enriched_at, enrich_method
                 FROM product_enriched WHERE rv_product_id = 14356""")
r = cur.fetchone()
print(f"=== product_enriched (14356) ===")
if r:
    for k, v in r.items():
        s = str(v)[:200]
        print(f"  {k:25s} = {s}")
else:
    print("  없음 (HTTP 500 으로 한 번도 저장되지 않음)")

conn.close()
