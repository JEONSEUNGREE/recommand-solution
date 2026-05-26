"""
기존 endorser 태그에 gender 필드가 없는 상품을 소급 업데이트.
태그에 이미 이름이 있으므로 "이 이름의 성별?" 만 Groq에 물어봄.

사용:
  python -m embedder.patch_endorser_gender [--dry-run]
"""
import argparse, json, os, sys, time
import psycopg, requests
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

OPENAI_CHAT_MODEL = os.environ.get("OPENAI_CHAT_MODEL", "gpt-4o-mini")

_GENDER_CACHE: dict[str, str] = {}


def _ask_gender(name: str) -> str:
    """연예인/그룹 이름 → '여성'|'남성'|'혼성'|'불명'"""
    if name in _GENDER_CACHE:
        return _GENDER_CACHE[name]

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return "불명"
    try:
        from openai import OpenAI
        client = OpenAI()
        r = client.chat.completions.create(
            model=OPENAI_CHAT_MODEL,
            messages=[
                {"role": "system", "content":
                    "너는 한국 연예인 전문가다. 주어진 한국 연예인/아이돌 그룹 이름의 성별을 판단하라.\n"
                    "여성 솔로/여성 그룹이면 '여성', 남성 솔로/남성 그룹이면 '남성', 혼성 그룹이면 '혼성', 모르면 '불명'.\n"
                    "JSON만 출력: {\"gender\": \"여성|남성|혼성|불명\"}"},
                {"role": "user", "content": name},
            ],
            temperature=0,
            response_format={"type": "json_object"},
            max_tokens=20,
        )
        g = json.loads(r.choices[0].message.content).get("gender", "불명")
    except Exception as e:
        g = "불명"
        print(f"    [WARN] {name} gender 조회 실패: {e}")

    _GENDER_CACHE[name] = g
    return g


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = psycopg.connect(DB_DSN)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""
            SELECT pe.rv_product_id, rp.product_name, pe.tags, pe.desc_persona
              FROM product_enriched pe
              JOIN rv_products rp ON rp.id = pe.rv_product_id
             WHERE pe.tags::text LIKE '%endorser%'
               AND pe.tags::text NOT LIKE '%"gender"%'
             ORDER BY pe.rv_product_id
        """)
        targets = cur.fetchall()

    print(f"gender 미설정 endorser 상품: {len(targets)}건")

    ok = fail = 0
    for row in targets:
        rv_id    = row["rv_product_id"]
        old_tags = row["tags"] or []

        # endorser 태그만 추출해서 이름별 gender 조회
        new_tags = []
        for t in old_tags:
            if t.get("tag_category") != "endorser":
                new_tags.append(t)
                continue
            name   = t["tag"]
            gender = _ask_gender(name)
            new_tags.append({**t, "gender": gender})

        endorser_names  = [(t["tag"], t["gender"]) for t in new_tags if t.get("tag_category") == "endorser"]
        gender_summary  = endorser_names[0][1] if endorser_names else "불명"

        # desc_persona: "착용 상품입니다." prefix 교체 (gender 추가)
        old_persona = row["desc_persona"] or ""
        # 기존 prefix 제거
        bare = old_persona
        if "착용 상품입니다." in bare:
            idx = bare.index("착용 상품입니다.")
            bare = bare[idx + len("착용 상품입니다."):].strip()

        who = " ".join(t["tag"] for t in new_tags if t.get("tag_category") == "endorser")
        gender_label = {"여성": "여성 연예인", "남성": "남성 연예인", "혼성": "혼성 그룹"}.get(
            gender_summary, "연예인"
        )
        new_persona = f"{who}({gender_label}) 착용 상품입니다. {bare}".strip()

        print(f"  rv_id={rv_id}  {row['product_name']}")
        print(f"    endorser: {endorser_names}")

        if args.dry_run:
            ok += 1
            continue

        try:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE product_enriched
                          SET tags = %s::jsonb, desc_persona = %s
                        WHERE rv_product_id = %s""",
                    (json.dumps(new_tags, ensure_ascii=False), new_persona, rv_id),
                )
            conn.commit()
            embed_one(conn, rv_id, "bge")
            ok += 1
        except Exception as e:
            fail += 1
            print(f"    FAIL: {e}")

    conn.close()
    print(f"\n완료 — OK={ok}  FAIL={fail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
