"""1만개 의류 상품 랜덤 생성 + Postgres에 적재."""
import os
import random
import string
from itertools import islice

import psycopg

DB_DSN = os.getenv("DB_DSN", "postgresql://app:app@localhost:5433/recommend")
TOTAL = int(os.getenv("TOTAL", "10000"))
BATCH = 500

CATEGORIES = {
    "상의":   ["티셔츠", "셔츠", "블라우스", "니트", "후드", "맨투맨", "가디건", "탱크탑"],
    "하의":   ["청바지", "슬랙스", "면바지", "트레이닝", "반바지", "스커트", "조거"],
    "아우터": ["자켓", "코트", "패딩", "블레이저", "점퍼", "무스탕", "트렌치코트", "베스트"],
    "원피스": ["미니원피스", "롱원피스", "셔츠원피스", "니트원피스", "점프수트"],
    "신발":   ["스니커즈", "구두", "부츠", "샌들", "슬리퍼", "로퍼", "워커"],
    "가방":   ["백팩", "토트백", "크로스백", "클러치", "에코백", "숄더백"],
    "액세서리": ["모자", "스카프", "벨트", "양말", "장갑", "머플러"],
}

BRANDS = ["무지샵", "콤마플레이스", "베이직룩", "데일리스타일", "모던시크",
          "어반패션", "클로젯9", "룩스앤", "핀크", "모먼트", "슬로우데이",
          "오프닝", "라이트하우스", "더네스트", "에이블리"]

GENDERS = ["여성", "남성", "공용"]
SEASONS = ["봄", "여름", "가을", "겨울", "사계절"]
STYLES  = ["캐주얼", "미니멀", "스트릿", "클래식", "빈티지", "페미닌",
           "스포티", "데일리", "오피스", "러블리", "모던", "보헤미안"]
COLORS  = ["블랙", "화이트", "그레이", "네이비", "베이지", "카키",
           "브라운", "블루", "핑크", "레드", "그린", "옐로우", "퍼플", "민트"]
MATERIALS_BY_CAT = {
    "상의":   ["면 100%", "코튼혼방", "린넨", "폴리에스터", "울혼방", "캐시미어", "니트"],
    "하의":   ["데님", "면", "폴리혼방", "스판", "린넨", "울"],
    "아우터": ["울 60%", "캐시미어 30%", "다운 90%", "폴리에스터", "양가죽", "트위드"],
    "원피스": ["시폰", "린넨", "폴리에스터", "면", "쉬폰혼방", "니트"],
    "신발":   ["가죽", "스웨이드", "캔버스", "메쉬", "합성피혁"],
    "가방":   ["가죽", "캔버스", "나일론", "PU레더", "리사이클 폴리"],
    "액세서리": ["면", "울", "아크릴", "가죽", "폴리"],
}

SHOE_SIZES   = ["220","225","230","235","240","245","250","255","260","265","270","275","280"]
APPAREL_SIZES = ["XS","S","M","L","XL","XXL","FREE"]

PRICE_RANGE = {
    "상의":   (15000, 120000),
    "하의":   (25000, 150000),
    "아우터": (80000, 600000),
    "원피스": (35000, 200000),
    "신발":   (50000, 350000),
    "가방":   (40000, 400000),
    "액세서리": (8000, 60000),
}

ADJECTIVES = ["슬림한", "오버핏", "루즈한", "베이직", "트렌디한", "포근한",
              "산뜻한", "모던한", "클래식한", "여유로운", "감각적인", "데일리한",
              "고급스러운", "캐주얼한", "심플한"]
DETAILS = ["부드러운 터치감", "신축성 좋은 원단", "쾌적한 착용감",
           "사계절 활용도 높은", "체형을 보완해주는", "포인트가 되는",
           "베이직한 디자인", "트렌디한 실루엣", "데일리로 활용하기 좋은"]


def make_sku() -> str:
    return "SKU-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=10))


def round_price(p: int) -> int:
    return (p // 1000) * 1000 + 900  # 끝자리 ,900원


def gen_product(idx: int) -> tuple:
    category = random.choice(list(CATEGORIES.keys()))
    subcategory = random.choice(CATEGORIES[category])
    brand = random.choice(BRANDS)
    gender = random.choices(GENDERS, weights=[5, 4, 3])[0]
    season = random.choices(SEASONS, weights=[3, 3, 3, 3, 2])[0]
    style = random.choice(STYLES)
    color = random.choice(COLORS)
    material = random.choice(MATERIALS_BY_CAT[category])

    sizes = random.sample(SHOE_SIZES, k=random.randint(3, 7)) if category == "신발" \
            else random.sample(APPAREL_SIZES, k=random.randint(2, 5))

    pmin, pmax = PRICE_RANGE[category]
    price = round_price(random.randint(pmin, pmax))
    sale = round_price(int(price * random.uniform(0.6, 0.95))) if random.random() < 0.35 else None
    stock = random.choices([0, random.randint(1, 200)], weights=[1, 9])[0]

    name = f"[{brand}] {color} {random.choice(ADJECTIVES)} {subcategory}"
    desc = (
        f"{brand}의 {style} {subcategory}. "
        f"{material} 소재로 {random.choice(DETAILS)} 제품입니다. "
        f"{season} 시즌 {gender} 코디에 활용하기 좋습니다. "
        f"컬러: {color}."
    )
    tags = [category, subcategory, brand, gender, season, style, color]

    return (
        make_sku(), name, brand, category, subcategory, gender, season,
        style, color, sizes, material, price, sale, stock, desc, tags,
    )


def chunked(it, size):
    it = iter(it)
    while batch := list(islice(it, size)):
        yield batch


def main():
    random.seed(42)
    print(f"Connecting → {DB_DSN}")
    with psycopg.connect(DB_DSN, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE products RESTART IDENTITY CASCADE;")

        sql = """
            INSERT INTO products
                (sku, name, brand, category, subcategory, gender, season,
                 style, color, sizes, material, price, sale_price, stock,
                 description, tags)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """
        gen = (gen_product(i) for i in range(TOTAL))
        inserted = 0
        for batch in chunked(gen, BATCH):
            with conn.cursor() as cur:
                cur.executemany(sql, batch)
            conn.commit()
            inserted += len(batch)
            print(f"  inserted {inserted}/{TOTAL}")

    print(f"Done: {TOTAL} products inserted.")


if __name__ == "__main__":
    main()
