"""
광고주 #3 생성: adv #1의 스크랩 데이터를 복사, LLM enrich는 빈 상태로.
파일시스템: D:\\recommand-data\\advertisers\\3  (junction → 1 또는 실제 디렉토리)
"""
import os
import subprocess
import sys

import psycopg

DB_DSN = "postgresql://app:app@localhost:5433/recommend"
DATA_DIR = r"D:\recommand-data\advertisers"
SRC_ADV_ID = 1
DST_ADV_ID = 3   # 원하는 id (BIGSERIAL이므로 실제 생성 후 확인)


def run(conn, sql, params=None, label=""):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        try:
            rows = cur.fetchall()
        except Exception:
            rows = None
    if label:
        print(f"[{label}] {'ok' if rows is None else rows}")
    return rows


def main():
    with psycopg.connect(DB_DSN) as conn:
        conn.autocommit = False

        # ── 0. 현재 상태 확인 ─────────────────────────────────
        rows = run(conn, "SELECT id, name FROM advertisers ORDER BY id", label="현재 광고주")
        print("  현재:", rows)

        src_count = run(conn, "SELECT COUNT(*) FROM rv_products WHERE advertiser_id=%s", (SRC_ADV_ID,))[0][0]
        print(f"  adv{SRC_ADV_ID} rv_products: {src_count:,}개")

        already = run(conn, "SELECT id FROM advertisers WHERE id=%s", (DST_ADV_ID,))
        if already:
            print(f"\n[경고] advertiser id={DST_ADV_ID} 이미 존재. 중단합니다.")
            print("  지우려면: DELETE FROM advertisers WHERE id=3;  (CASCADE로 rv_products도 삭제됨)")
            return

        # ── 1. advertisers 행 생성 (id=3 강제 지정) ──────────
        print(f"\n[1] advertiser #{DST_ADV_ID} 생성...")
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO advertisers (id, name, host_type, shop_url, shop_key, license_key, notes)
                SELECT %s, name || ' [LLM테스트]', host_type, shop_url, shop_key, license_key,
                       'adv1 동일 스크랩 데이터, LLM 재정제 테스트용'
                FROM advertisers WHERE id = %s
                RETURNING id, name
            """, (DST_ADV_ID, SRC_ADV_ID))
            created = cur.fetchone()
        print(f"  생성됨: id={created[0]}, name={created[1]}")

        # BIGSERIAL 시퀀스 동기화 (혹시 id를 수동 지정했으므로)
        with conn.cursor() as cur:
            cur.execute("SELECT setval(pg_get_serial_sequence('advertisers','id'), (SELECT MAX(id) FROM advertisers))")

        # ── 2. rv_products 복사 + id 매핑 ─────────────────────
        print(f"\n[2] rv_products 복사 중 (adv{SRC_ADV_ID} → adv{DST_ADV_ID})...")
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TEMP TABLE adv_id_map (old_id BIGINT, new_id BIGINT, product_code TEXT)
                ON COMMIT DROP
            """)

            cur.execute("""
                WITH ins AS (
                  INSERT INTO rv_products (
                    advertiser_id, product_code, product_origin_code, product_name,
                    company_nm, price, sale_price, stock, product_url, image_url, raw_payload,
                    body_text, body_text_len, html_path, image_dir, image_local_count,
                    scrape_status, scraped_at, scrape_error,
                    enrich_method, enriched_info, enriched_at, enrich_error
                  )
                  SELECT
                    %s, product_code, product_origin_code, product_name,
                    company_nm, price, sale_price, stock, product_url, image_url, raw_payload,
                    body_text, body_text_len,
                    REPLACE(REPLACE(html_path,  '/advertisers/1/', '/advertisers/3/'),
                                               '\\advertisers\\1\\', '\\advertisers\\3\\'),
                    REPLACE(REPLACE(image_dir,  '/advertisers/1/', '/advertisers/3/'),
                                               '\\advertisers\\1\\', '\\advertisers\\3\\'),
                    image_local_count,
                    scrape_status, scraped_at, scrape_error,
                    enrich_method, enriched_info, enriched_at, enrich_error
                  FROM rv_products WHERE advertiser_id = %s
                  RETURNING id, product_code
                )
                INSERT INTO adv_id_map (new_id, product_code)
                SELECT id, product_code FROM ins
            """, (DST_ADV_ID, SRC_ADV_ID))

            # old_id 채우기
            cur.execute("""
                UPDATE adv_id_map m
                SET old_id = old.id
                FROM rv_products old
                WHERE old.advertiser_id = %s AND old.product_code = m.product_code
            """, (SRC_ADV_ID,))

            cur.execute("SELECT COUNT(*) FROM adv_id_map")
            n_products = cur.fetchone()[0]
        print(f"  rv_products 복사: {n_products:,}개")

        # ── 3. rv_product_images 복사 ──────────────────────────
        print(f"\n[3] rv_product_images 복사 중...")
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO rv_product_images (advertiser_id, product_code, rv_product_id, src_url, local_filename)
                SELECT %s, rpi.product_code, m.new_id, rpi.src_url, rpi.local_filename
                FROM rv_product_images rpi
                JOIN adv_id_map m ON m.product_code = rpi.product_code
                WHERE rpi.advertiser_id = %s
                ON CONFLICT DO NOTHING
            """, (DST_ADV_ID, SRC_ADV_ID))
            n_images = cur.rowcount
        print(f"  rv_product_images 복사: {n_images:,}개")

        # ── 4. advertiser_image_blocks 복사 ───────────────────
        print(f"\n[4] advertiser_image_blocks 복사 중...")
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO advertiser_image_blocks (advertiser_id, pattern, enabled, notes)
                SELECT %s, pattern, enabled, notes
                FROM advertiser_image_blocks WHERE advertiser_id = %s
                ON CONFLICT DO NOTHING
            """, (DST_ADV_ID, SRC_ADV_ID))
            n_blocks = cur.rowcount
        print(f"  image_blocks 복사: {n_blocks}개")

        # ── 5. BIGSERIAL 시퀀스 동기화 ────────────────────────
        with conn.cursor() as cur:
            cur.execute("SELECT setval(pg_get_serial_sequence('rv_products','id'), (SELECT MAX(id) FROM rv_products))")
            cur.execute("SELECT setval(pg_get_serial_sequence('rv_product_images','id'), (SELECT MAX(id) FROM rv_product_images))")

        conn.commit()
        print("\n[DB] 커밋 완료 ✓")

        # ── 6. 최종 확인 ──────────────────────────────────────
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM rv_products WHERE advertiser_id=%s", (DST_ADV_ID,))
            print(f"  adv{DST_ADV_ID} rv_products: {cur.fetchone()[0]:,}개")
            cur.execute("SELECT COUNT(*) FROM rv_product_images WHERE advertiser_id=%s", (DST_ADV_ID,))
            print(f"  adv{DST_ADV_ID} rv_product_images: {cur.fetchone()[0]:,}개")
            cur.execute("SELECT COUNT(*) FROM product_enriched WHERE advertiser_id=%s", (DST_ADV_ID,))
            print(f"  adv{DST_ADV_ID} product_enriched: {cur.fetchone()[0]}개 (빈 상태)")

    # ── 7. 파일시스템 junction 생성 ───────────────────────────
    src_dir = os.path.join(DATA_DIR, str(SRC_ADV_ID))
    dst_dir = os.path.join(DATA_DIR, str(DST_ADV_ID))

    print(f"\n[7] 파일시스템 설정: {dst_dir}")
    if os.path.exists(dst_dir):
        print(f"  이미 존재: {dst_dir} — 건너뜀")
    elif not os.path.exists(src_dir):
        print(f"  [경고] 소스 디렉토리 없음: {src_dir}")
    else:
        # Windows junction point (심링크와 달리 관리자 권한 불필요)
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", dst_dir, src_dir],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            print(f"  Junction 생성 완료: {dst_dir} → {src_dir}")
        else:
            print(f"  [실패] {result.stderr.strip()}")
            print(f"  수동으로 실행하세요: mklink /J \"{dst_dir}\" \"{src_dir}\"")

    print("\n완료! 광고주 #3에서 batch-enrich 실행하면 product_enriched에 새 결과가 쌓입니다.")


if __name__ == "__main__":
    main()
