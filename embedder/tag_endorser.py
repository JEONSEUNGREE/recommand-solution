"""
상품명에 한국 연예인/유명인 이름이 포함된 경우 endorser 태그를 자동 추가 후 재임베딩.

- LLM(Groq)이 상품명을 보고 연예인 여부 + 이름 + 성별 추출
- product_enriched.tags에 {"tag": "이름", "tag_category": "endorser", "gender": "여성|남성|혼성"} 추가
- product_enriched.desc_persona 앞에 착용자 문장 prefix
- product_descriptions 재임베딩

사용:
  python -m embedder.tag_endorser [--dry-run] [--limit N] [--착용-only]
"""

import argparse, json, os, sys, time
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

OPENAI_CHAT_MODEL = os.environ.get("OPENAI_CHAT_MODEL", "gpt-4o-mini")

_SYSTEM = """너는 한국 연예인·아이돌·인플루언서 전문가다.
주어진 상품명에 실제 한국 연예인, 아이돌 그룹/멤버, 인플루언서, 유명인 이름이 포함되어 있는지 판단하라.

규칙:
- 그룹명과 멤버명을 구분해서 추출한다 (예: 그룹="하츠투하츠", 멤버=["유하"])
- 개인 활동명이면 group=null, names에 이름만
- "착용", "협찬", "추천", "선택" 같은 동사는 제외하고 이름만 추출
- 일반 단어(화이트골드, 착용 등)나 브랜드명은 연예인으로 보지 않는다
- 확실하지 않으면 found=false
- gender: 그룹/개인 전체 기준 ("여성"=여성 솔로 or 여성 그룹, "남성"=남성 솔로 or 남성 그룹, "혼성"=혼성 그룹, null=불명)

JSON만 출력 (코드펜스 없이):
{"found": true/false, "group": "그룹명 또는 null", "names": ["이름1", "이름2"], "gender": "여성|남성|혼성|null"}

예시:
상품명: "하츠투하츠 유하 화이트골드 착용 RA0462" → {"found":true,"group":"하츠투하츠","names":["유하"],"gender":"여성"}
상품명: "트와이스 나연 옐로우골드 착용 EA2453" → {"found":true,"group":"트와이스","names":["나연"],"gender":"여성"}
상품명: "정용화 화이트골드 착용 RA0666" → {"found":true,"group":null,"names":["정용화"],"gender":"남성"}
상품명: "몬스타엑스 형원 옐로우골드 착용 EA2629" → {"found":true,"group":"몬스타엑스","names":["형원"],"gender":"남성"}
상품명: "손예진 핑크골드 착용 EA1228" → {"found":true,"group":null,"names":["손예진"],"gender":"여성"}
상품명: "SPRING SALE 14K 귀걸이/목걸이 SET 지토 레이어드 SET SET0498" → {"found":false,"group":null,"names":[],"gender":null}
상품명: "루브르파리 소가죽100% 데일리추천 파우치 BG0026_C" → {"found":false,"group":null,"names":[],"gender":null}"""


def _ask_llm(product_name: str) -> dict:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return {"found": False, "group": None, "names": [], "gender": None}
    try:
        from openai import OpenAI
        client = OpenAI()
        r = client.chat.completions.create(
            model=OPENAI_CHAT_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": f"상품명: {product_name}"},
            ],
            temperature=0,
            response_format={"type": "json_object"},
            max_tokens=120,
        )
        return json.loads(r.choices[0].message.content)
    except Exception as e:
        return {"found": False, "group": None, "names": [], "gender": None, "_error": str(e)}


def _build_endorser_tags(result: dict) -> list[dict]:
    gender = result.get("gender")
    tags = []
    if result.get("group"):
        tags.append({
            "tag": result["group"],
            "tag_category": "endorser",
            "confidence": 1.0,
            "gender": gender,
        })
    for name in result.get("names") or []:
        if name != result.get("group"):
            tags.append({
                "tag": name,
                "tag_category": "endorser",
                "confidence": 1.0,
                "gender": gender,
            })
    return tags


def _prepend_persona(existing: str | None, result: dict) -> str:
    parts = []
    if result.get("group"):
        parts.append(result["group"])
    if result.get("names"):
        parts.extend(n for n in result["names"] if n != result.get("group"))
    who = " ".join(parts)
    gender_str = {"여성": "여성 연예인", "남성": "남성 연예인", "혼성": "혼성 그룹"}.get(
        result.get("gender", "") or "", "연예인"
    )
    prefix = f"{who}({gender_str}) 착용 상품입니다."
    return f"{prefix} {existing}" if existing else prefix


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="처리 최대 건수 (0=전체)")
    ap.add_argument("--착용-only", action="store_true", dest="착용_only",
                    help="상품명에 '착용' 포함된 것만 처리")
    args = ap.parse_args()

    conn = psycopg.connect(DB_DSN)
    with conn.cursor(row_factory=dict_row) as cur:
        name_filter = "AND rp.product_name LIKE '%착용%'" if args.착용_only else ""
        cur.execute(f"""
            SELECT pe.rv_product_id, rp.product_name
              FROM product_enriched pe
              JOIN rv_products rp ON rp.id = pe.rv_product_id
             WHERE pe.enrich_status = 'embedded'
               AND NOT (pe.tags::text LIKE '%endorser%')
               {name_filter}
             ORDER BY pe.rv_product_id
        """)
        targets = cur.fetchall()

    if args.limit:
        targets = targets[:args.limit]

    print(f"대상: {len(targets)}건")

    found_cnt = skip_cnt = fail_cnt = 0

    for i, row in enumerate(targets, 1):
        rv_id = row["rv_product_id"]
        name  = row["product_name"]

        result = _ask_llm(name)
        time.sleep(0.05)

        if not result.get("found"):
            skip_cnt += 1
            if i % 100 == 0:
                print(f"  [{i}/{len(targets)}] 진행 중... (연예인 없음 {skip_cnt}건 스킵)")
            continue

        new_tags = _build_endorser_tags(result)
        if not new_tags:
            skip_cnt += 1
            continue

        tag_names = [t["tag"] for t in new_tags]
        gender = result.get("gender") or "불명"
        print(f"  [{i}/{len(targets)}] rv_id={rv_id}  {name}")
        print(f"    → {result.get('group')} / {result.get('names')}  성별={gender}  tags={tag_names}")

        if args.dry_run:
            found_cnt += 1
            continue

        try:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT tags, desc_persona FROM product_enriched WHERE rv_product_id=%s",
                    (rv_id,),
                )
                pe = cur.fetchone()
                old_tags    = pe["tags"] or []
                new_all     = old_tags + new_tags
                new_persona = _prepend_persona(pe["desc_persona"], result)

                cur.execute(
                    """UPDATE product_enriched
                          SET tags = %s::jsonb,
                              desc_persona = %s
                        WHERE rv_product_id = %s""",
                    (json.dumps(new_all, ensure_ascii=False), new_persona, rv_id),
                )
                conn.commit()

            embed_one(conn, rv_id, "bge")
            found_cnt += 1
        except Exception as e:
            fail_cnt += 1
            print(f"    FAIL: {e}")

    conn.close()
    print(f"\n완료 — 연예인 발견·처리={found_cnt}  스킵={skip_cnt}  실패={fail_cnt}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
