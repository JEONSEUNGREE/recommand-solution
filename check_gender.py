import sys, psycopg, json
from psycopg.rows import dict_row
from dotenv import load_dotenv
load_dotenv()
for s in (sys.stdout, sys.stderr):
    try: s.reconfigure(encoding="utf-8", errors="replace")
    except: pass
from embedder.enrich_claude import DB_DSN
from embedder.embed_descriptions import embed_one

conn = psycopg.connect(DB_DSN)
with conn.cursor(row_factory=dict_row) as cur:
    cur.execute("""
        SELECT pe.rv_product_id, rp.product_code, rp.product_name, pe.tags, pe.desc_persona
          FROM product_enriched pe
          JOIN rv_products rp ON rp.id = pe.rv_product_id
         WHERE pe.tags::text LIKE '%하츠투하츠%'
         ORDER BY pe.rv_product_id
    """)
    rows = cur.fetchall()

print(f"하츠투하츠 상품: {len(rows)}건")
for r in rows:
    print(f"  rv_id={r['rv_product_id']}  code={r['product_code']}  {r['product_name']}")
    for t in (r['tags'] or []):
        if t.get('tag_category') == 'endorser':
            print(f"    endorser: {t['tag']} / gender={t.get('gender')}")

print("\n수정 시작...")
for r in rows:
    rv_id = r['rv_product_id']
    old_tags = r['tags'] or []
    new_tags = []
    changed = False
    for t in old_tags:
        if t.get('tag_category') == 'endorser' and t.get('tag') == '하츠투하츠' and t.get('gender') != '여성':
            new_tags.append({**t, 'gender': '여성'})
            changed = True
            print(f"  rv_id={rv_id} 하츠투하츠 {t.get('gender')}→여성")
        else:
            new_tags.append(t)

    if not changed:
        print(f"  rv_id={rv_id} 이미 정상")
        continue

    old_persona = r['desc_persona'] or ''
    bare = old_persona
    if '착용 상품입니다.' in bare:
        idx = bare.index('착용 상품입니다.')
        bare = bare[idx + len('착용 상품입니다.'):].strip()

    endorser_tags = [t for t in new_tags if t.get('tag_category') == 'endorser']
    who = ' '.join(t['tag'] for t in endorser_tags)
    gender_label = '여성 연예인'
    new_persona = f"{who}({gender_label}) 착용 상품입니다. {bare}".strip()

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE product_enriched SET tags=%s::jsonb, desc_persona=%s WHERE rv_product_id=%s",
            (json.dumps(new_tags, ensure_ascii=False), new_persona, rv_id),
        )
    conn.commit()
    embed_one(conn, rv_id, 'bge')
    print(f"  rv_id={rv_id} 수정+재임베딩 완료")

conn.close()
