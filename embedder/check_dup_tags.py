import psycopg2, os, sys

conn = psycopg2.connect("postgresql://app:app@localhost:5433/recommend")
cur = conn.cursor()
cur.execute(r"""
SELECT
  regexp_replace(lower(
    CASE WHEN jsonb_typeof(t) = 'string' THEN t #>> '{}' ELSE t->>'tag' END
  ), '\s+', '', 'g') AS norm,
  array_agg(DISTINCT
    CASE WHEN jsonb_typeof(t) = 'string' THEN t #>> '{}' ELSE t->>'tag' END
  ) AS variants,
  COUNT(*) AS cnt
FROM product_enriched pe,
     jsonb_array_elements(pe.tags) AS t
WHERE pe.enrich_status = 'embedded'
  AND pe.tags IS NOT NULL
GROUP BY norm
HAVING COUNT(DISTINCT
    CASE WHEN jsonb_typeof(t) = 'string' THEN t #>> '{}' ELSE t->>'tag' END
  ) > 1
ORDER BY cnt DESC
LIMIT 60
""")
rows = cur.fetchall()
print(f"{'cnt':>4}  {'normalized':30s}  variants")
print("-"*80)
for norm, variants, cnt in rows:
    print(f"{cnt:4d}  {norm:30s}  {variants}")
conn.close()
