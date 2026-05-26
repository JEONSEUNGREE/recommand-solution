const currentRequestJson = `POST /search-rv-llm
{
  "query": "손예진 착용한 것 같은 고급스러운 5만원 이하 로즈골드 귀걸이",
  "advertiser_id": 1,
  "backend": "bge",
  "llm_provider": "groq",
  "k": 10
}`;

const currentParsedJson = `{
  "semantic_query": "고급스러운 로즈골드 귀걸이",
  "filters": {
    "target_gender": null,
    "target_age_min": null,
    "target_age_max": null,
    "category_contains": "귀걸이",
    "perspective_preference": "style",
    "keyword_filters": ["손예진"]
  },
  "notes": "5만원 이하 가격 조건은 현재 구현에서는 정형 필터로 적용되지 않음"
}`;

const currentResponseJson = `{
  "backend": "bge",
  "dim": 1024,
  "advertiser_id": 1,
  "k": 10,
  "raw_query": "손예진 착용한 것 같은 고급스러운 5만원 이하 로즈골드 귀걸이",
  "parsed": { "...": "LLM 구조화 결과" },
  "keyword_filters_applied": ["손예진"],
  "keyword_filter_relaxed": false,
  "pe_filter_relaxed": false,
  "debug_sql": "WITH best AS (...) SELECT ...",
  "items": [
    {
      "rv_product_id": 123,
      "advertiser_id": 1,
      "perspective": "style",
      "description": "로즈골드 톤과 섬세한 디자인으로 세련된 무드를 주는 귀걸이",
      "distance": 0.231,
      "product_name": "14K 로즈골드 귀걸이",
      "product_code": "2280472",
      "price": 69800,
      "sale_price": 49800,
      "category": "귀걸이",
      "brand": "루브르파리",
      "target_gender": "여성",
      "tags": [{ "tag": "셀럽착용", "tag_category": "feature" }]
    }
  ]
}`;

const currentSql = `WITH all_dist AS (
    SELECT
        pd.rv_product_id,
        pd.advertiser_id,
        pd.perspective,
        pd.description,
        pd.embedding_bge <=> %s::vector AS distance
    FROM product_descriptions pd
    LEFT JOIN product_enriched pe ON pe.rv_product_id = pd.rv_product_id
    WHERE pd.embedding_bge IS NOT NULL
      AND pd.advertiser_id = %s
      AND pe.category ILIKE %s
      AND (
          pd.description ILIKE %s
          OR pe.tags::text ILIKE %s
      )
),
ranked AS (
    SELECT
        rv_product_id,
        advertiser_id,
        AVG(distance) AS distance,
        (array_agg(perspective ORDER BY distance))[1] AS perspective,
        (array_agg(description ORDER BY distance))[1] AS description
    FROM all_dist
    GROUP BY rv_product_id, advertiser_id
)
SELECT
    r.rv_product_id,
    r.advertiser_id,
    r.perspective,
    r.description,
    r.distance,
    rv.product_code,
    rv.product_name,
    rv.product_url,
    rv.image_url,
    rv.price,
    rv.sale_price,
    pe.category,
    pe.brand,
    pe.brand_tier,
    pe.target_gender,
    pe.target_age_min,
    pe.target_age_max,
    pe.tags,
    pe.desc_persona
FROM ranked r
JOIN rv_products rv ON rv.id = r.rv_product_id
LEFT JOIN product_enriched pe ON pe.rv_product_id = r.rv_product_id
ORDER BY r.distance ASC
LIMIT %s;`;

const storedProductJson = `{
  "category": { "name": "주얼리 > 귀걸이", "confidence": 0.94 },
  "common_attributes": {
    "brand": "루브르파리",
    "brand_tier": "중가",
    "price_tier": "5만원대",
    "target_gender": "여성",
    "target_age_min": 20,
    "target_age_max": 40
  },
  "category_attributes": {
    "metal": "14K",
    "color": "로즈골드",
    "closure_type": "침형",
    "allergy_safe": null
  },
  "tags": [
    { "tag": "데이트룩", "tag_category": "situation", "confidence": 0.88 },
    { "tag": "고급스러운", "tag_category": "mood", "confidence": 0.82 },
    { "tag": "로즈골드", "tag_category": "feature", "confidence": 0.91 }
  ],
  "descriptions": [
    { "perspective": "situation", "description": "봄 데이트와 모임에 어울리는 은은한 로즈골드 귀걸이" },
    { "perspective": "material", "description": "14K 소재와 로즈골드 컬러가 부드러운 광택을 만든다" },
    { "perspective": "style", "description": "작지만 고급스럽고 여성스러운 포인트를 주는 디자인" },
    { "perspective": "persona", "description": "과하지 않은 주얼리를 찾는 20~40대 여성에게 적합하다" }
  ]
}`;

const storageSql = `-- 1. LLM JSON은 product_enriched에 구조화 저장
INSERT INTO product_enriched (
  rv_product_id,
  advertiser_id,
  product_code,
  category,
  brand,
  brand_tier,
  price_tier,
  target_gender,
  target_age_min,
  target_age_max,
  category_attributes,
  tags,
  desc_situation,
  desc_material,
  desc_style,
  desc_persona
) VALUES (...);

-- 2. desc_* 4개 텍스트만 product_descriptions에 관점별 저장
INSERT INTO product_descriptions (
  rv_product_id,
  advertiser_id,
  perspective,
  description,
  embedding_bge,
  embedding_openai
) VALUES
  (123, 1, 'situation', '봄 데이트와 모임에...', '[1024차원]'::vector, NULL),
  (123, 1, 'material',  '14K 소재와...',       '[1024차원]'::vector, NULL),
  (123, 1, 'style',     '작지만 고급스럽고...', '[1024차원]'::vector, NULL),
  (123, 1, 'persona',   '과하지 않은...',       '[1024차원]'::vector, NULL);`;

const improvedContractJson = `{
  "intent": "product_search",
  "raw_query": "나한테는 너무 비싸지 않고 예쁜 출근용 가방 추천해줘",
  "normalized_query": "출근용 가방 단정한 디자인 적정 가격",
  "semantic": {
    "query": "출근용 가방 단정하고 예쁜 스타일",
    "perspective_weights": {
      "situation": 0.35,
      "material": 0.10,
      "style": 0.35,
      "persona": 0.20
    }
  },
  "hard_filters": {
    "category_ids": ["bag"],
    "price": {
      "operator": "<=",
      "value": 80000,
      "source": "personal_price_profile"
    },
    "availability": "in_stock",
    "policy_safe": true
  },
  "soft_preferences": {
    "mood": ["예쁜", "단정한"],
    "use_case": ["출근"],
    "price_sensitivity": "value_for_money",
    "brand_affinity": [],
    "avoid": ["과한 로고", "무거운 가방"]
  },
  "personalization": {
    "user_id": "u_123",
    "price_band": { "low": 30000, "mid": 80000, "high": 180000 },
    "preferred_categories": ["bag", "shoes"],
    "preferred_colors": ["black", "ivory"],
    "size_profile": {},
    "negative_feedback": ["너무 화려함", "배송 느림"]
  },
  "ranking": {
    "vector_weight": 0.55,
    "business_weight": 0.10,
    "personal_weight": 0.25,
    "freshness_weight": 0.05,
    "quality_weight": 0.05
  },
  "safety": {
    "toxicity_action": "sanitize_or_reject",
    "adult_action": "block_if_minor",
    "medical_claim_action": "downgrade_unverified"
  }
}`;

const improvedSql = `WITH candidates AS (
    SELECT
        pd.rv_product_id,
        pd.advertiser_id,
        MIN(pd.embedding_bge <=> :query_vec::vector) AS vector_distance
    FROM product_descriptions pd
    JOIN rv_products rv ON rv.id = pd.rv_product_id
    JOIN product_enriched pe ON pe.rv_product_id = pd.rv_product_id
    LEFT JOIN product_policy pp ON pp.rv_product_id = pd.rv_product_id
    WHERE pd.embedding_bge IS NOT NULL
      AND rv.stock > 0
      AND pe.category_id = ANY(:category_ids)
      AND COALESCE(rv.sale_price, rv.price) <= :personal_price_max
      AND COALESCE(pp.is_blocked, false) = false
    GROUP BY pd.rv_product_id, pd.advertiser_id
),
scored AS (
    SELECT
        c.rv_product_id,
        c.vector_distance,
        1 - c.vector_distance AS semantic_score,
        up.preference_score,
        pq.quality_score,
        bs.business_score,
        (
          (1 - c.vector_distance) * 0.55
          + COALESCE(up.preference_score, 0) * 0.25
          + COALESCE(bs.business_score, 0) * 0.10
          + COALESCE(pq.quality_score, 0) * 0.10
        ) AS final_score
    FROM candidates c
    LEFT JOIN user_product_preferences up
      ON up.user_id = :user_id AND up.rv_product_id = c.rv_product_id
    LEFT JOIN product_quality pq ON pq.rv_product_id = c.rv_product_id
    LEFT JOIN business_scores bs ON bs.rv_product_id = c.rv_product_id
)
SELECT *
FROM scored
ORDER BY final_score DESC
LIMIT :k;`;

const pipelineSteps = [
  {
    title: "1. 입력 수집",
    body: "프론트에서 query, advertiser_id, backend, llm_provider, k를 전송한다.",
    detail: "현재 페이지 기준 LLM 모드면 /search-rv-llm, 일반 모드면 /search-rv를 호출한다.",
  },
  {
    title: "2. LLM 구조화",
    body: "사용자 문장을 semantic_query와 filters로 분리한다.",
    detail: "현재 구현 필터는 성별, 연령, 카테고리 포함어, 관점 선호, 키워드 하드필터 중심이다.",
  },
  {
    title: "3. 임베딩",
    body: "semantic_query만 bge-m3 또는 OpenAI embedding으로 벡터화한다.",
    detail: "JSON 전체를 벡터화하지 않는다. 자연어 의미 문장만 vector 컬럼과 비교한다.",
  },
  {
    title: "4. DB 후보 제한",
    body: "product_descriptions, product_enriched, rv_products를 조인한다.",
    detail: "광고주, 카테고리, 연령, 성별, 명시 키워드는 SQL WHERE 조건으로 후보를 줄인다.",
  },
  {
    title: "5. 벡터 랭킹",
    body: "pd.embedding_bge <=> query_vector 거리로 유사도를 계산한다.",
    detail: "상품당 situation/material/style/persona 4개 관점 거리를 평균내고, 가까운 순으로 정렬한다.",
  },
  {
    title: "6. 결과/디버그",
    body: "items와 debug_sql, parsed JSON, 필터 완화 여부를 반환한다.",
    detail: "현재 구현은 결과 0건이면 일부 키워드/정형 필터를 완화할 수 있다.",
  },
];

const currentFilters = [
  ["advertiser_id", "하드", "pd.advertiser_id = ?", "광고주 선택"],
  ["target_gender", "하드", "pe.target_gender = ?", "여성/남성/공용"],
  ["target_age_min/max", "하드", "pe.target_age_max >= min AND pe.target_age_min <= max", "20대, 30~40대"],
  ["category_contains", "하드", "pe.category ILIKE '%키워드%'", "귀걸이, 원피스, 가방"],
  ["keyword_filters", "하드에 가까움", "pd.description ILIKE OR pe.tags::text ILIKE", "연예인명, 14K, 니켈프리"],
  ["perspective_preference", "현재는 파싱 중심", "검색 관점 가중치로 발전 필요", "style/material/persona/situation"],
  ["semantic_query", "벡터", "pd.embedding_* <=> query_vector", "고급스러운 로즈골드 귀걸이"],
];

const proposedFilters = [
  ["가격", "개인별 가격대, 절대 가격, 할인율, 배송비 포함가, 객단가 대비 비쌈/저렴함", "비싸다/가성비/부담없다를 사용자별 숫자로 변환"],
  ["카테고리", "표준 category_id, 원본 category, 상하위 카테고리, 세트 상품 구성", "ILIKE 대신 정규화 테이블 필요"],
  ["속성", "색상, 소재, 사이즈, 핏, 길이, 계절, 방수, 알러지, 14K/925/니켈프리", "틀리면 안 되는 속성은 must_match"],
  ["재고/판매", "재고, 품절, 옵션별 재고, 판매중지, 예약배송, 배송 리드타임", "추천 결과 품질의 기본 게이트"],
  ["개인화", "가격 민감도, 선호 브랜드, 사이즈, 색상, 구매이력, 클릭, 찜, 반품, 싫어요", "하드필터보다 점수 보정이 안전"],
  ["상황", "출근, 데이트, 여행, 운동, 결혼식, 선물, 계절, 날씨", "semantic query와 situation 관점 가중치"],
  ["스타일/감성", "예쁜, 힙한, 단정한, 고급스러운, 귀여운, 미니멀", "동의어 사전 + 벡터 + 클릭 피드백"],
  ["품질", "리뷰 평점, 리뷰 수, 반품률, 불량률, 이미지 품질, 설명 신뢰도", "낮으면 랭킹 페널티"],
  ["정책/안전", "욕설, 혐오, 성인, 불법, 의료/효능 과장, 민감정보", "입력 차단/정제와 상품 노출 차단 분리"],
  ["비즈니스", "마진, 광고 계약, 신상품, 프로모션, 재고 소진, 다양성", "사용자 만족 점수를 해치지 않는 범위에서 반영"],
  ["다양성", "같은 상품/브랜드/카테고리 중복 제한, 가격대 분산", "추천 리스트가 한쪽으로 몰리는 문제 방지"],
  ["설명 가능성", "왜 추천됐는지, 어떤 필터가 적용됐는지, 어떤 조건이 완화됐는지", "CS와 디버깅에 중요"],
];

const strengtheningPlan = [
  {
    title: "가격 추상어를 개인별 숫자로 바꾸기",
    body: "비싸다, 저렴하다, 가성비는 전역 기준이 아니라 사용자/카테고리별 기준이다. user_price_profile 테이블을 만들고 카테고리별 low/mid/high를 저장해야 한다.",
    code: `user_price_profile(user_id, category_id, low_max, mid_max, high_min)
query: "부담없는 출근 가방"
=> category=bag, price_max = user_price_profile.mid_max`,
  },
  {
    title: "필터를 hard, must, soft, negative로 분리",
    body: "14K, 니켈프리, 5만원 이하는 틀리면 안 되는 조건이고, 예쁜/고급스러운은 점수 보정이다. 같은 filters 배열에 섞으면 0건 완화와 랭킹이 불안정해진다.",
    code: `hard_filters: 재고, 가격, 성인 차단
must_match: 14K, 니켈프리, 특정 인물 착용
soft_preferences: 예쁜, 고급스러운, 데일리
negative_preferences: 과한 로고, 너무 짧은 기장`,
  },
  {
    title: "정규화 속성 테이블 추가",
    body: "현재 category/tags JSONB와 ILIKE만으로는 '실버'와 'silver', '은', '925'를 안정적으로 묶기 어렵다. LLM 결과를 product_attributes로 펼쳐야 한다.",
    code: `product_attributes(
  rv_product_id,
  attr_key,
  attr_value,
  normalized_value,
  confidence,
  source
)`,
  },
  {
    title: "욕설/정책 필터를 입력과 상품 양쪽에 적용",
    body: "사용자 쿼리 욕설은 검색어 정제 또는 거절 대상이고, 상품 설명의 금칙 표현은 노출/랭킹 정책 대상이다. 둘을 같은 필터로 처리하면 안 된다.",
    code: `query_safety: clean | sanitized | rejected
product_policy: blocked_reason, age_gate, claim_risk, profanity_score`,
  },
  {
    title: "점수식을 명시적으로 운영",
    body: "벡터 거리만으로 추천하면 개인화와 품질, 비즈니스 조건을 반영하기 어렵다. 후보 생성은 벡터+필터, 최종 정렬은 weighted ranking으로 나누는 편이 좋다.",
    code: `final_score =
  semantic_score * 0.55
  + personal_score * 0.25
  + quality_score * 0.10
  + business_score * 0.10`,
  },
  {
    title: "필터 완화 규칙을 명시",
    body: "0건일 때 무엇을 풀 수 있는지 정책이 있어야 한다. 가격은 10% 완화 가능, 특정 인물/소재/알러지 조건은 완화 금지처럼 등급화한다.",
    code: `relaxable: mood, color_family, price_by_10_percent
non_relaxable: adult_safety, allergy_safe, exact_material, named_person`,
  },
];

function CodeBlock({ code, tone = "neutral" }: { code: string; tone?: "neutral" | "green" | "amber" }) {
  const toneClass =
    tone === "green"
      ? "border-emerald-900/70 bg-emerald-950/25 text-emerald-100"
      : tone === "amber"
        ? "border-amber-900/70 bg-amber-950/20 text-amber-100"
        : "border-neutral-800 bg-neutral-950 text-neutral-300";

  return (
    <pre className={`overflow-x-auto rounded border p-4 text-xs leading-5 ${toneClass}`}>
      <code>{code}</code>
    </pre>
  );
}

export default function QueryPipelinePage() {
  return (
    <main className="min-h-screen bg-neutral-950 text-neutral-100">
      <div className="mx-auto max-w-7xl px-6 py-8">
        <header className="mb-7 border-b border-neutral-800 pb-5">
          <p className="text-xs font-semibold uppercase text-indigo-400">
            Recommendation Query Pipeline
          </p>
          <h1 className="mt-2 text-2xl font-semibold">LLM JSON, SQL 필터, 벡터 검색이 나뉘어 동작하는 방식</h1>
          <p className="mt-2 max-w-4xl text-sm leading-6 text-neutral-400">
            현재 구조는 자연어를 LLM으로 JSON화하고, 의미 조건은 임베딩 검색으로 보내며, 정확 조건은 RDB 필터로 처리한다.
            이 페이지는 현재 구현 기준의 실제 요청/응답/SQL과 앞으로 강화해야 할 필터 설계를 같이 정리한다.
          </p>
        </header>

        <section className="grid gap-3 lg:grid-cols-6">
          {pipelineSteps.map((step) => (
            <article key={step.title} className="rounded-lg border border-neutral-800 bg-neutral-900 p-4">
              <h2 className="text-sm font-semibold text-neutral-100">{step.title}</h2>
              <p className="mt-2 text-sm font-medium text-indigo-200">{step.body}</p>
              <p className="mt-3 text-xs leading-5 text-neutral-500">{step.detail}</p>
            </article>
          ))}
        </section>

        <section className="mt-7">
          <div className="mb-3 border-b border-neutral-800 pb-3">
            <p className="text-xs font-semibold uppercase text-indigo-400">Current Flow</p>
            <h2 className="mt-1 text-lg font-semibold">현재 `/search-rv-llm` 동작 예시</h2>
          </div>
          <div className="grid gap-4 lg:grid-cols-3">
            <article>
              <h3 className="mb-2 text-sm font-semibold">1. 프론트 요청 JSON</h3>
              <CodeBlock code={currentRequestJson} />
            </article>
            <article>
              <h3 className="mb-2 text-sm font-semibold">2. LLM 파싱 JSON</h3>
              <CodeBlock code={currentParsedJson} tone="green" />
            </article>
            <article>
              <h3 className="mb-2 text-sm font-semibold">3. API 응답 JSON</h3>
              <CodeBlock code={currentResponseJson} tone="amber" />
            </article>
          </div>
        </section>

        <section className="mt-7 grid gap-4 lg:grid-cols-[1.05fr_0.95fr]">
          <article>
            <div className="mb-3 border-b border-neutral-800 pb-3">
              <p className="text-xs font-semibold uppercase text-indigo-400">SQL</p>
              <h2 className="mt-1 text-lg font-semibold">현재 검색 SQL 핵심</h2>
            </div>
            <CodeBlock code={currentSql} />
          </article>

          <article>
            <div className="mb-3 border-b border-neutral-800 pb-3">
              <p className="text-xs font-semibold uppercase text-indigo-400">Stored Data</p>
              <h2 className="mt-1 text-lg font-semibold">상품 LLM JSON 저장 방식</h2>
            </div>
            <div className="space-y-3">
              <CodeBlock code={storedProductJson} tone="green" />
              <CodeBlock code={storageSql} />
            </div>
          </article>
        </section>

        <section className="mt-7 rounded-lg border border-neutral-800 bg-neutral-900">
          <div className="border-b border-neutral-800 px-4 py-3">
            <p className="text-xs font-semibold uppercase text-indigo-400">Implemented Filters</p>
            <h2 className="mt-1 text-lg font-semibold">현재 코드가 실제로 쓰는 필터</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-sm">
              <thead className="bg-neutral-950 text-xs text-neutral-400">
                <tr>
                  <th className="px-4 py-3 font-semibold">필드</th>
                  <th className="px-4 py-3 font-semibold">역할</th>
                  <th className="px-4 py-3 font-semibold">처리</th>
                  <th className="px-4 py-3 font-semibold">예시</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-neutral-800">
                {currentFilters.map(([field, role, process, example]) => (
                  <tr key={field}>
                    <td className="px-4 py-3 font-mono text-xs text-indigo-300">{field}</td>
                    <td className="px-4 py-3 text-neutral-300">{role}</td>
                    <td className="px-4 py-3 font-mono text-xs text-neutral-400">{process}</td>
                    <td className="px-4 py-3 text-neutral-300">{example}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="mt-8">
          <div className="mb-3 border-b border-neutral-800 pb-3">
            <p className="text-xs font-semibold uppercase text-indigo-400">Target Contract</p>
            <h2 className="mt-1 text-lg font-semibold">강화된 검색 계약 JSON 제안</h2>
            <p className="mt-2 max-w-4xl text-sm leading-6 text-neutral-400">
              지금보다 안정적으로 운영하려면 `filters` 하나에 다 넣지 말고, 정확 조건, 선호 조건, 개인화, 정책, 랭킹을 분리해야 한다.
            </p>
          </div>
          <div className="grid gap-4 lg:grid-cols-[0.95fr_1.05fr]">
            <CodeBlock code={improvedContractJson} tone="green" />
            <CodeBlock code={improvedSql} />
          </div>
        </section>

        <section className="mt-7">
          <div className="mb-3 border-b border-neutral-800 pb-3">
            <p className="text-xs font-semibold uppercase text-indigo-400">Filter Surface</p>
            <h2 className="mt-1 text-lg font-semibold">고려해야 할 필터 범위</h2>
          </div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {proposedFilters.map(([title, body, note]) => (
              <article key={title} className="rounded-lg border border-neutral-800 bg-neutral-900 p-4">
                <h3 className="text-sm font-semibold text-neutral-100">{title}</h3>
                <p className="mt-2 text-sm leading-6 text-neutral-300">{body}</p>
                <p className="mt-3 text-xs leading-5 text-neutral-500">{note}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="mt-7">
          <div className="mb-3 border-b border-neutral-800 pb-3">
            <p className="text-xs font-semibold uppercase text-indigo-400">Strengthening Plan</p>
            <h2 className="mt-1 text-lg font-semibold">추천 검색 강화 방법</h2>
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            {strengtheningPlan.map((item) => (
              <article key={item.title} className="rounded-lg border border-neutral-800 bg-neutral-900 p-4">
                <h3 className="text-sm font-semibold text-neutral-100">{item.title}</h3>
                <p className="mt-2 text-sm leading-6 text-neutral-300">{item.body}</p>
                <div className="mt-3">
                  <CodeBlock code={item.code} tone="amber" />
                </div>
              </article>
            ))}
          </div>
        </section>

        <section className="mt-7 grid gap-4 lg:grid-cols-3">
          <article className="rounded-lg border border-red-900/60 bg-red-950/20 p-4">
            <h2 className="text-sm font-semibold text-red-200">완화하면 안 되는 조건</h2>
            <p className="mt-2 text-xs leading-5 text-neutral-400">
              성인/불법/욕설 정책, 알러지 안전, 특정 소재, 특정 인물 착용, 가격 상한처럼 사용자가 명시한 안전·정확 조건은 결과가 0건이어도 임의로 풀면 안 된다.
            </p>
          </article>
          <article className="rounded-lg border border-blue-900/60 bg-blue-950/20 p-4">
            <h2 className="text-sm font-semibold text-blue-200">점수로 처리할 조건</h2>
            <p className="mt-2 text-xs leading-5 text-neutral-400">
              예쁜, 고급스러운, 트렌디한, 선물하기 좋은, 나한테 어울리는 같은 조건은 SQL 하드필터보다 벡터 유사도와 개인화 점수로 반영하는 편이 안정적이다.
            </p>
          </article>
          <article className="rounded-lg border border-emerald-900/60 bg-emerald-950/20 p-4">
            <h2 className="text-sm font-semibold text-emerald-200">운영에 필요한 로그</h2>
            <p className="mt-2 text-xs leading-5 text-neutral-400">
              원문 쿼리, LLM JSON, 적용 필터, 완화된 필터, 후보 수, 최종 점수 구성, 클릭/구매/반품 피드백을 남겨야 추천 품질을 개선할 수 있다.
            </p>
          </article>
        </section>
      </div>
    </main>
  );
}
