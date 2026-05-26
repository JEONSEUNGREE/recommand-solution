"use client";

import { useEffect, useState } from "react";

const EMBEDDER_BASE = process.env.NEXT_PUBLIC_EMBEDDER_BASE ?? "/api/embedder";

type Advertiser = { id: number; name: string; host_type: string; shop_url: string; enriched_count: number };

type SearchItem = {
  rv_product_id: number;
  advertiser_id: number;
  perspective: "situation" | "material" | "style" | "persona";
  description: string;
  distance: number;
  product_code: string;
  product_name: string;
  product_url: string;
  image_url: string | null;
  price: number | null;
  sale_price: number | null;
  category: string | null;
  brand: string | null;
  brand_tier: string | null;
  target_gender: string | null;
  target_age_min: number | null;
  target_age_max: number | null;
};

type Detail = {
  rv_product_id: number;
  advertiser_id: number;
  product_code: string;
  product_name: string;
  product_url: string;
  image_url: string | null;
  category: string | null;
  category_confidence: number | null;
  brand: string | null;
  brand_tier: string | null;
  price_tier: string | null;
  target_gender: string | null;
  target_age_min: number | null;
  target_age_max: number | null;
  origin_country: string | null;
  manufacturer: string | null;
  category_attributes: Record<string, unknown> | null;
  compatible_products: unknown[] | null;
  set_components: unknown[] | null;
  tags: { tag: string; tag_category: string; confidence?: number }[] | null;
  desc_situation: string | null;
  desc_material: string | null;
  desc_style: string | null;
  desc_persona: string | null;
  image_types: { sha1: string; image_type: string; content_description: string; is_main_product: boolean; used_for_attributes: boolean }[] | null;
  model_used: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cost_usd: number | null;
  duration_ms: number | null;
  enriched_at: string | null;
  descriptions_meta: { perspective: string; desc_len: number; has_bge: boolean; has_openai: boolean }[];
};

const BACKEND_API = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8090";

export default function SearchRvPage() {
  const [advertisers, setAdvertisers] = useState<Advertiser[]>([]);
  const [advertiserId, setAdvertiserId] = useState<number | "all">("all");
  const [backend, setBackend] = useState<"bge" | "openai">("bge");
  const [perspective, setPerspective] = useState<"" | "situation" | "material" | "style" | "persona">("");
  const [k, setK] = useState(10);
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<SearchItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [useLlm, setUseLlm] = useState(false);
  const [llmProvider, setLlmProvider] = useState<"groq" | "openai">("groq");
  const [parsed, setParsed] = useState<{
    semantic_query?: string;
    filters?: {
      brand_tier?: string | null;
      target_gender?: string | null;
      target_age_min?: number | null;
      target_age_max?: number | null;
      category_contains?: string | null;
      perspective_preference?: string | null;
      keyword_filters?: string[];
    };
    notes?: string;
    provider?: string;
  } | null>(null);
  const [debugInfo, setDebugInfo] = useState<{
    raw_query?: string;
    semantic_query?: string;
    embed_backend?: string;
    embed_dim?: number;
    keyword_filters_applied?: string[];
    keyword_filter_relaxed?: boolean;
    pe_filter_relaxed?: boolean;
    parsed_raw?: unknown;
    debug_sql?: string;
  } | null>(null);
  const [showDebug, setShowDebug] = useState(false);

  const [detail, setDetail] = useState<Detail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  useEffect(() => {
    fetch(`${EMBEDDER_BASE}/advertisers-rv`).then(r => r.json()).then(setAdvertisers).catch(() => {});
  }, []);

  async function search() {
    if (!query.trim()) return;
    setLoading(true); setError(null); setItems([]); setParsed(null);
    try {
      const body: Record<string, unknown> = { query: query.trim(), backend, k };
      if (advertiserId !== "all") body.advertiser_id = advertiserId;
      if (!useLlm && perspective) body.perspective = perspective;
      if (useLlm) body.llm_provider = llmProvider;
      const endpoint = useLlm ? "/search-rv-llm" : "/search-rv";
      const r = await fetch(`${EMBEDDER_BASE}${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json; charset=utf-8" },
        body: JSON.stringify(body),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || `HTTP ${r.status}`);
      setItems(j.items || []);
      if (useLlm) {
        setParsed(j.parsed || null);
        setDebugInfo({
          raw_query: j.raw_query,
          semantic_query: j.parsed?.semantic_query,
          embed_backend: `${j.backend} (${j.dim}d)`,
          keyword_filters_applied: j.keyword_filters_applied,
          keyword_filter_relaxed: j.keyword_filter_relaxed,
          pe_filter_relaxed: j.pe_filter_relaxed,
          parsed_raw: j.parsed,
          debug_sql: j.debug_sql,
        });
      } else {
        setParsed(null);
        setDebugInfo({
          raw_query: query.trim(),
          semantic_query: query.trim(),
          embed_backend: `${j.backend} (${j.dim}d)`,
          debug_sql: j.debug_sql,
        });
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  async function openDetail(rvId: number) {
    setDetail(null); setDetailLoading(true);
    try {
      const r = await fetch(`${EMBEDDER_BASE}/products-rv/${rvId}/detail`);
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || `HTTP ${r.status}`);
      setDetail(j);
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    } finally {
      setDetailLoading(false);
    }
  }

  function imgSrc(adv: number, code: string, sha1: string) {
    return `${BACKEND_API}/advertisers/${adv}/products/${code}/images/${sha1}.jpg`;
  }

  return (
    <main className="mx-auto max-w-6xl p-6 font-sans">
      <header className="mb-6">
        <a href="/" className="text-xs text-blue-600">← 홈</a>
        <h1 className="text-2xl font-bold mt-1">광고주 상품 검색 (RV)</h1>
        <p className="text-xs text-gray-500 mt-1">
          rv_products + product_enriched + product_descriptions · 광고주 / 임베딩 모델 선택
        </p>
      </header>

      <section className="mb-4 p-3 rounded border bg-white space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-gray-600">광고주</span>
          <select
            value={advertiserId === "all" ? "all" : String(advertiserId)}
            onChange={(e) => setAdvertiserId(e.target.value === "all" ? "all" : Number(e.target.value))}
            className="text-xs border-gray-300 rounded px-2 py-1"
          >
            <option value="all">전체 광고주</option>
            {advertisers.map((a) => (
              <option key={a.id} value={a.id}>#{a.id} {a.name} ({a.enriched_count}개 enriched)</option>
            ))}
          </select>

          <span className="text-xs text-gray-600 ml-2">임베딩</span>
          <select value={backend} onChange={(e) => setBackend(e.target.value as "bge" | "openai")} className="text-xs border-gray-300 rounded px-2 py-1">
            <option value="bge">bge-m3 (1024d, 로컬)</option>
            <option value="openai">openai text-embedding-3-small (1536d)</option>
          </select>

          <span className="text-xs text-gray-600 ml-2">관점</span>
          <select value={perspective} onChange={(e) => setPerspective(e.target.value as typeof perspective)} className="text-xs border-gray-300 rounded px-2 py-1">
            <option value="">전체</option>
            <option value="situation">situation</option>
            <option value="material">material</option>
            <option value="style">style</option>
            <option value="persona">persona</option>
          </select>

          <span className="text-xs text-gray-600 ml-2">k</span>
          <input type="number" min={1} max={100} value={k} onChange={(e) => setK(Number(e.target.value) || 10)} className="w-14 text-xs border-gray-300 rounded px-1 py-0.5 text-right" />

          <label className="flex items-center gap-1 ml-2 text-xs cursor-pointer">
            <input type="checkbox" checked={useLlm} onChange={(e) => setUseLlm(e.target.checked)} />
            <span className={useLlm ? "text-indigo-700 font-semibold" : "text-gray-600"}>🤖 LLM 변환</span>
          </label>
          {useLlm && (
            <select
              value={llmProvider}
              onChange={(e) => setLlmProvider(e.target.value as "groq" | "openai")}
              className="text-xs border-gray-300 rounded px-2 py-1"
              title="자연어를 정제 쿼리+필터로 변환할 LLM 선택"
            >
              <option value="groq">Groq Llama-3.3</option>
              <option value="openai">OpenAI gpt-4o-mini</option>
            </select>
          )}
        </div>

        {debugInfo && (
          <div className="text-xs border rounded overflow-hidden">
            <button
              onClick={() => setShowDebug((v) => !v)}
              className="w-full flex items-center justify-between px-3 py-1.5 bg-gray-50 hover:bg-gray-100 font-mono text-gray-600"
            >
              <span>
                🔍 파이프라인 디버그
                {debugInfo.keyword_filter_relaxed && (
                  <span className="ml-2 text-amber-600">⚠ 키워드필터 완화됨</span>
                )}
              </span>
              <span>{showDebug ? "▲" : "▼"}</span>
            </button>
            {showDebug && (
              <div className="p-3 bg-gray-50 border-t space-y-2 font-mono">
                {/* Step 1: 원본 쿼리 */}
                <div>
                  <span className="text-gray-400">① 원본 쿼리</span>
                  <span className="ml-2 text-gray-800 font-bold">{debugInfo.raw_query}</span>
                </div>

                {/* Step 2: LLM 변환 결과 (LLM 모드에서만) */}
                {useLlm && debugInfo.parsed_raw && (
                  <div>
                    <div className="text-gray-400 mb-1">② LLM 변환 ({parsed?.provider ?? llmProvider})</div>
                    <pre className="bg-white border rounded p-2 text-[11px] overflow-x-auto whitespace-pre-wrap">
                      {JSON.stringify(debugInfo.parsed_raw, null, 2)}
                    </pre>
                  </div>
                )}

                {/* Step 3: 임베딩 입력 */}
                <div>
                  <span className="text-gray-400">③ 임베딩 입력 ({debugInfo.embed_backend})</span>
                  <div className="mt-0.5 px-2 py-1 bg-indigo-50 border border-indigo-200 rounded text-indigo-800 font-bold">
                    &quot;{debugInfo.semantic_query}&quot;
                  </div>
                </div>

                {/* Step 4: 키워드 하드필터 */}
                <div>
                  <span className="text-gray-400">④ 키워드 하드필터 (SQL ILIKE)</span>
                  {debugInfo.keyword_filters_applied && debugInfo.keyword_filters_applied.length > 0 ? (
                    <div className="flex flex-wrap gap-1 mt-0.5">
                      {debugInfo.keyword_filters_applied.map((kw) => (
                        <span key={kw} className="px-2 py-0.5 bg-orange-100 text-orange-700 rounded">{kw}</span>
                      ))}
                      {debugInfo.keyword_filter_relaxed && (
                        <span className="px-2 py-0.5 bg-amber-100 text-amber-700 rounded">→ 0건으로 완화됨</span>
                      )}
                    </div>
                  ) : (
                    <span className="ml-2 text-gray-400">없음 (전체 검색)</span>
                  )}
                </div>

                {/* Step 5: 실제 SQL */}
                {debugInfo.debug_sql && (
                  <div>
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-gray-400">⑤ 실제 실행 SQL (DBeaver 복붙 가능)</span>
                      <button
                        onClick={() => navigator.clipboard.writeText(debugInfo.debug_sql!)}
                        className="text-[11px] px-2 py-0.5 rounded bg-gray-200 hover:bg-gray-300 text-gray-700"
                      >
                        복사
                      </button>
                    </div>
                    <textarea
                      readOnly
                      value={debugInfo.debug_sql}
                      className="w-full h-48 text-[11px] bg-white border rounded p-2 font-mono resize-y"
                    />
                  </div>
                )}
              </div>
            )}
          </div>
        )}
        <div className="flex gap-2">
          <input
            type="text"
            placeholder="자연어로 검색 (예: 봄에 데일리로 가벼운 14k 귀걸이)"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") search(); }}
            className="flex-1 border-gray-300 rounded px-3 py-2 text-sm"
          />
          <button onClick={search} disabled={loading} className="px-4 py-2 rounded bg-indigo-600 text-white text-sm disabled:opacity-40">
            {loading ? "검색 중..." : "검색"}
          </button>
        </div>
        {error && <p className="text-xs text-red-700 font-mono">{error}</p>}
      </section>

      {/* LLM 해석 요약 바 */}
      {useLlm && parsed && (
        <div className="mb-3 p-3 rounded border border-indigo-200 bg-indigo-50 text-xs space-y-1.5">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-semibold text-indigo-700">🤖 LLM 해석</span>
            <span className="text-indigo-900 font-medium">
              &quot;{parsed.semantic_query}&quot;
            </span>
            {debugInfo?.keyword_filter_relaxed && (
              <span className="px-1.5 py-0.5 bg-amber-100 text-amber-700 rounded">⚠ 키워드필터 완화</span>
            )}
            {debugInfo?.pe_filter_relaxed && (
              <span className="px-1.5 py-0.5 bg-red-100 text-red-700 rounded">⚠ 정형필터 완화</span>
            )}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {parsed.filters?.brand_tier && (
              <span className="px-2 py-0.5 rounded bg-amber-100 text-amber-800">브랜드: {parsed.filters.brand_tier}</span>
            )}
            {parsed.filters?.target_gender && (
              <span className="px-2 py-0.5 rounded bg-pink-100 text-pink-800">성별: {parsed.filters.target_gender}</span>
            )}
            {(parsed.filters?.target_age_min || parsed.filters?.target_age_max) && (
              <span className="px-2 py-0.5 rounded bg-blue-100 text-blue-800">
                연령: {parsed.filters.target_age_min ?? "?"}~{parsed.filters.target_age_max ?? "?"}대
              </span>
            )}
            {parsed.filters?.category_contains && (
              <span className="px-2 py-0.5 rounded bg-indigo-100 text-indigo-800">카테고리: {parsed.filters.category_contains}</span>
            )}
            {parsed.filters?.perspective_preference && (
              <span className="px-2 py-0.5 rounded bg-purple-100 text-purple-800">관점: {parsed.filters.perspective_preference}</span>
            )}
            {(parsed.filters?.keyword_filters ?? []).map((kw: string) => (
              <span key={kw} className="px-2 py-0.5 rounded bg-orange-100 text-orange-800">키워드: {kw}</span>
            ))}
            {!parsed.filters?.brand_tier && !parsed.filters?.target_gender && !parsed.filters?.category_contains &&
             !parsed.filters?.target_age_min && !parsed.filters?.target_age_max &&
             !(parsed.filters?.keyword_filters?.length) && (
              <span className="text-indigo-400">필터 없음 (순수 벡터 검색)</span>
            )}
          </div>
          {parsed.notes && <div className="text-indigo-500 italic">{parsed.notes}</div>}
        </div>
      )}

      <ul className="space-y-2">
        {items.map((it, idx) => (
          <li key={`${it.rv_product_id}-${it.perspective}-${idx}`} className="p-3 border rounded text-sm bg-white">
            <div className="flex gap-3">
              {it.image_url ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={it.image_url} alt="" className="w-16 h-16 object-cover rounded bg-gray-100 shrink-0" />
              ) : <div className="w-16 h-16 bg-gray-100 rounded shrink-0" />}
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-medium truncate">{it.product_name}</span>
                  <span className="text-xs font-mono text-gray-400">#{it.product_code}</span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-700 font-mono">
                    {it.perspective} · dist {it.distance.toFixed(3)}
                  </span>
                </div>
                {/* 가격 */}
                {(it.sale_price != null || it.price != null) && (
                  <div className="flex items-baseline gap-1.5 mt-1">
                    {it.sale_price != null && (
                      <span className="text-sm font-bold text-red-600">
                        {it.sale_price.toLocaleString()}원
                      </span>
                    )}
                    {it.price != null && it.sale_price != null && it.price !== it.sale_price && (
                      <span className="text-xs text-gray-400 line-through">
                        {it.price.toLocaleString()}원
                      </span>
                    )}
                    {it.price != null && it.sale_price == null && (
                      <span className="text-sm font-bold text-gray-800">
                        {it.price.toLocaleString()}원
                      </span>
                    )}
                    {it.price != null && it.sale_price != null && it.price !== it.sale_price && (
                      <span className="text-[10px] text-red-500 font-medium">
                        {Math.round((1 - it.sale_price / it.price) * 100)}% 할인
                      </span>
                    )}
                  </div>
                )}
                <div className="flex flex-wrap gap-1 mt-1 text-[11px]">
                  {it.category && (
                    <span className="px-1.5 py-0.5 rounded bg-indigo-50 text-indigo-700 font-medium border border-indigo-200">
                      📂 {it.category}
                    </span>
                  )}
                  {it.brand && <span className="px-1.5 py-0.5 rounded bg-purple-50 text-purple-700">{it.brand}</span>}
                  {it.brand_tier && <span className="px-1.5 py-0.5 rounded bg-amber-50 text-amber-700">{it.brand_tier}</span>}
                  {it.target_gender && <span className="px-1.5 py-0.5 rounded bg-pink-50 text-pink-700">{it.target_gender}</span>}
                  {it.target_age_min && it.target_age_max && (
                    <span className="px-1.5 py-0.5 rounded bg-blue-50 text-blue-700">{it.target_age_min}~{it.target_age_max}대</span>
                  )}
                </div>
                <p className="text-xs text-gray-600 mt-1 line-clamp-3">{it.description}</p>
              </div>
              <div className="shrink-0">
                <button onClick={() => openDetail(it.rv_product_id)} className="px-3 py-1 rounded bg-gray-800 text-white text-xs">
                  상세
                </button>
              </div>
            </div>
          </li>
        ))}
        {!loading && !items.length && (
          <li className="text-center text-gray-400 py-12 text-sm">검색해보세요</li>
        )}
      </ul>

      {/* 상세 모달 */}
      {(detail || detailLoading) && (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4" onClick={() => setDetail(null)}>
          <div className="bg-white rounded-lg max-w-5xl w-full max-h-[92vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
            <header className="p-4 border-b flex items-center justify-between">
              <div className="min-w-0 flex-1">
                <h3 className="font-semibold truncate">{detail?.product_name ?? "로딩 중..."}</h3>
                {detail && (
                  <p className="text-xs text-gray-500 font-mono">
                    rv_id={detail.rv_product_id} · #{detail.product_code} · advertiser={detail.advertiser_id} ·
                    enriched={detail.enriched_at?.slice(0,16) ?? "-"} · cost ${detail.cost_usd?.toFixed(4) ?? "?"} · {detail.duration_ms ?? "?"}ms
                  </p>
                )}
              </div>
              <button onClick={() => setDetail(null)} className="px-3 py-1 rounded border text-sm">닫기</button>
            </header>
            <div className="p-4 overflow-y-auto flex-1 space-y-4 text-sm">
              {detailLoading && <p className="text-gray-500">로딩 중...</p>}
              {detail && (
                <>
                  {/* 헤더 메타 */}
                  <section>
                    <h4 className="font-semibold mb-1">카테고리 / 공통 속성</h4>
                    <div className="flex flex-wrap gap-1 text-xs">
                      {detail.category && <span className="px-2 py-0.5 rounded bg-gray-100">{detail.category} (conf {detail.category_confidence?.toFixed(2)})</span>}
                      {detail.brand && <span className="px-2 py-0.5 rounded bg-purple-50 text-purple-700">brand: {detail.brand}</span>}
                      {detail.brand_tier && <span className="px-2 py-0.5 rounded bg-amber-50 text-amber-700">tier: {detail.brand_tier}</span>}
                      {detail.target_gender && <span className="px-2 py-0.5 rounded bg-pink-50 text-pink-700">{detail.target_gender}</span>}
                      {(detail.target_age_min && detail.target_age_max) && (
                        <span className="px-2 py-0.5 rounded bg-blue-50 text-blue-700">{detail.target_age_min}~{detail.target_age_max}대</span>
                      )}
                      {detail.origin_country && <span className="px-2 py-0.5 rounded bg-gray-100">{detail.origin_country}</span>}
                      {detail.manufacturer && <span className="px-2 py-0.5 rounded bg-gray-100">{detail.manufacturer}</span>}
                    </div>
                  </section>

                  {/* 카테고리별 속성 */}
                  {detail.category_attributes && Object.keys(detail.category_attributes).length > 0 && (
                    <section>
                      <h4 className="font-semibold mb-1">category_attributes (JSONB)</h4>
                      <pre className="text-xs bg-gray-50 p-2 rounded overflow-x-auto">{JSON.stringify(detail.category_attributes, null, 2)}</pre>
                    </section>
                  )}

                  {/* SET 구성 / 관련 상품 */}
                  {detail.set_components && detail.set_components.length > 0 && (
                    <section>
                      <h4 className="font-semibold mb-1">SET 구성</h4>
                      <pre className="text-xs bg-gray-50 p-2 rounded overflow-x-auto">{JSON.stringify(detail.set_components, null, 2)}</pre>
                    </section>
                  )}
                  {detail.compatible_products && detail.compatible_products.length > 0 && (
                    <section>
                      <h4 className="font-semibold mb-1">호환/관련 상품 (오염 격리)</h4>
                      <pre className="text-xs bg-amber-50 p-2 rounded overflow-x-auto">{JSON.stringify(detail.compatible_products, null, 2)}</pre>
                    </section>
                  )}

                  {/* 4관점 설명문 + 벡터 메타 */}
                  <section>
                    <h4 className="font-semibold mb-1">다관점 설명문 (벡터 임베딩)</h4>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                      {[
                        { p: "situation", t: detail.desc_situation },
                        { p: "material", t: detail.desc_material },
                        { p: "style", t: detail.desc_style },
                        { p: "persona", t: detail.desc_persona },
                      ].map(({ p, t }) => {
                        const m = detail.descriptions_meta?.find((x) => x.perspective === p);
                        return (
                          <div key={p} className="border rounded p-2 bg-gray-50">
                            <div className="flex items-center justify-between text-[11px] mb-1">
                              <span className="font-mono text-indigo-700">{p}</span>
                              <span className="font-mono text-gray-500">
                                {m ? `bge ${m.has_bge ? "✓" : "✗"} · openai ${m.has_openai ? "✓" : "✗"}` : "no vector"}
                              </span>
                            </div>
                            <p className="text-xs">{t}</p>
                          </div>
                        );
                      })}
                    </div>
                  </section>

                  {/* 태그 */}
                  {detail.tags && detail.tags.length > 0 && (
                    <section>
                      <h4 className="font-semibold mb-1">태그 ({detail.tags.length}개)</h4>
                      <div className="flex flex-wrap gap-1 text-[11px]">
                        {detail.tags.map((t, i) => (
                          <span
                            key={i}
                            className={`px-1.5 py-0.5 rounded font-mono ${t.tag_category === "situation" ? "bg-blue-50 text-blue-700" : t.tag_category === "mood" ? "bg-pink-50 text-pink-700" : "bg-green-50 text-green-700"}`}
                            title={`${t.tag_category}${t.confidence ? ` · ${t.confidence.toFixed(2)}` : ""}`}
                          >
                            {t.tag}
                          </span>
                        ))}
                      </div>
                    </section>
                  )}

                  {/* 이미지 메타 */}
                  {detail.image_types && detail.image_types.length > 0 && (
                    <section>
                      <h4 className="font-semibold mb-1">이미지 분류 ({detail.image_types.length}장)</h4>
                      <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 gap-2">
                        {detail.image_types.map((im) => {
                          const used = im.used_for_attributes && im.is_main_product;
                          return (
                            <div key={im.sha1} className={`border rounded overflow-hidden text-[10px] ${used ? "border-green-300" : "border-amber-300 opacity-70"}`}>
                              {/* eslint-disable-next-line @next/next/no-img-element */}
                              <img src={imgSrc(detail.advertiser_id, detail.product_code, im.sha1)} alt="" className="w-full h-20 object-cover bg-gray-100" />
                              <div className="p-1">
                                <div className="flex items-center justify-between">
                                  <span className="font-mono text-indigo-700">{im.image_type}</span>
                                  <span className={`font-mono ${used ? "text-green-700" : "text-amber-700"}`}>{used ? "✓" : "skip"}</span>
                                </div>
                                <p className="text-gray-600 line-clamp-2" title={im.content_description}>{im.content_description}</p>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </section>
                  )}

                  {/* 비용 / 토큰 */}
                  <section className="text-xs text-gray-500 border-t pt-2">
                    <div className="flex flex-wrap gap-3 font-mono">
                      <span>model: {detail.model_used || "-"}</span>
                      <span>in: {detail.input_tokens || 0} tokens</span>
                      <span>out: {detail.output_tokens || 0} tokens</span>
                      <span>cost: ${detail.cost_usd?.toFixed(4) || "?"}</span>
                      <span>time: {detail.duration_ms ? `${(detail.duration_ms / 1000).toFixed(1)}s` : "?"}</span>
                      <a href={detail.product_url} target="_blank" rel="noreferrer" className="text-blue-600 underline">원본 페이지</a>
                    </div>
                  </section>
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
