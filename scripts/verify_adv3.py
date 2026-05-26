import psycopg
DB_DSN = "postgresql://app:app@localhost:5433/recommend"
with psycopg.connect(DB_DSN) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT id, name FROM advertisers ORDER BY id")
        print("advertisers:", cur.fetchall())
        cur.execute("SELECT COUNT(*) FROM rv_products WHERE advertiser_id=3")
        print("adv3 rv_products:", cur.fetchone()[0])
        cur.execute("SELECT COUNT(*) FROM rv_product_images WHERE advertiser_id=3")
        print("adv3 rv_product_images:", cur.fetchone()[0])
        cur.execute("SELECT COUNT(*) FROM product_enriched WHERE advertiser_id=3")
        print("adv3 product_enriched:", cur.fetchone()[0], "(empty - OK)")
        cur.execute("SELECT COUNT(*) FROM advertiser_image_blocks WHERE advertiser_id=3")
        print("adv3 image_blocks:", cur.fetchone()[0])
        cur.execute("SELECT scrape_status, COUNT(*) FROM rv_products WHERE advertiser_id=3 GROUP BY scrape_status")
        print("adv3 scrape_status:", cur.fetchall())
        cur.execute("SELECT html_path, image_dir FROM rv_products WHERE advertiser_id=3 AND html_path IS NOT NULL LIMIT 2")
        for row in cur.fetchall():
            print("  html_path:", row[0])
            print("  image_dir:", row[1])
