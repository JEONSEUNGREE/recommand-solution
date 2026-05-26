"use client";

import { useEffect, useState } from "react";

const EMBEDDER_BASE = process.env.NEXT_PUBLIC_EMBEDDER_BASE ?? "/api/embedder";
const BACKEND_API = process.env.NEXT_PUBLIC_API_BASE ?? "http://192.168.101.27:8090";

function imgSrc(adv: number, code: string, sha1: string) {
  return `${BACKEND_API}/advertisers/${adv}/products/${code}/images/${sha1}.jpg`;
}

type Advertiser = { id: number; name: string; host_type: string; enriched_count: number };

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
  target_gender: string | null;
  target_age_min: number | null;
  target_age_max: number | null;
};

type Detail = {
  rv_product_id: number; advertiser_id: number; product_code: string;
  product_name: string; product_url: string; image_url: string | null;
  category: string | null; category_confidence: number | null;
  brand: string | null; brand_tier: string | null; price_tier: string | null;
  target_gender: string | null; target_age_min: number | null; target_age_max: number | null;
  origin_country: string | null; manufacturer: string | null;
  category_attributes: Record<string, unknown> | null;
  compatible_products: unknown[] | null; set_components: unknown[] | null;
  tags: { tag: string; tag_category: string; confidence?: number }[] | null;
  desc_situation: string | null; desc_material: string | null;
  desc_style: string | null; desc_persona: string | null;
  image_types: { sha1: string; image_type: string; content_description: string; is_main_product: boolean; used_for_attributes: boolean }[] | null;
  model_used: string | null; input_tokens: number | null; output_tokens: number | null;
  cost_usd: number | null; duration_ms: number | null; enriched_at: string | null;
  descriptions_meta: { perspective: string; desc_len: number; has_bge: boolean; has_openai: boolean }[];
};

const PERSPECTIVE_LABEL: Record<string, string> = {
  situation: "상황", material: "소재", style: "스타일", persona: "페르소나",
};

const SAMPLE_QUERIES = [
  "봄 데이트에 어울리는 14k 귀걸이",
  "연예인 착용 목걸이",
  "30대 직장 여성 데일리 귀걸이",
  "비싸보이지만 저렴한 귀걸이",
  "미니멀 실버 귀걸이",
];

export default function Home() {
  const [advertisers, setAdvertisers] = useState<Advertiser[]>([]);
  const [advertiserId, setAdvertiserId] = useState<number | "all">("all");
  const [backend, setBackend] = useState<"bge" | "openai">("bge");
  const [k, setK] = useState(10);
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<SearchItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [useLlm, setUseLlm] = useState(true);
  const [llmProvider, setLlmProvider] = useState<"groq" | "openai">("groq");
  const [parsed, setParsed] = useState<{
    semantic_query?: string;
    filters?: {
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
    keyword_filter_relaxed?: boolean;
    pe_filter_relaxed?: boolean;
  } | null>(null);
  const [selectedItem, setSelectedItem] = useState<SearchItem | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  useEffect(() => {
    fetch(`${EMBEDDER_BASE}/advertisers-rv`)
      .then((r) => r.json()).then(setAdvertisers).catch(() => {});
  }, []);

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

  async function search(q?: string) {
    const queryText = (q ?? query).trim();
    if (!queryText) return;
    if (q) setQuery(q);
    setLoading(true); setError(null); setItems([]); setParsed(null); setDebugInfo(null); setSelectedItem(null);
    try {
      const body: Record<string, unknown> = { query: queryText, backend, k };
      if (advertiserId !== "all") body.advertiser_id = advertiserId;
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
        setDebugInfo({ keyword_filter_relaxed: j.keyword_filter_relaxed, pe_filter_relaxed: j.pe_filter_relaxed });
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100 flex flex-col">
      {/* 헤더 */}
      <header className="border-b border-neutral-800 px-6 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">상품 추천 검색</h1>
          <p className="text-xs text-neutral-400 mt-0.5">pgvector · bge-m3 · 4관점 임베딩</p>
        </div>
        <div className="flex items-center gap-3 text-sm">
          <a href="/products" className="text-neutral-400 hover:text-white transition">상품 관리</a>
        </div>
      </header>

      {/* 검색 설정 */}
      <div className="border-b border-neutral-800 px-6 py-3 space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          {/* 광고주 */}
          <select
            value={advertiserId === "all" ? "all" : String(advertiserId)}
            onChange={(e) => setAdvertiserId(e.target.value === "all" ? "all" : Number(e.target.value))}
            className="bg-neutral-900 border border-neutral-700 rounded-lg px-3 py-1.5 text-sm focus:border-indigo-500 focus:outline-none"
          >
            <option value="all">전체 광고주</option>
            {advertisers.map((a) => (
              <option key={a.id} value={a.id}>#{a.id} {a.name} ({a.enriched_count}개)</option>
            ))}
          </select>

          {/* 임베딩 */}
          <select
            value={backend}
            onChange={(e) => setBackend(e.target.value as "bge" | "openai")}
            className="bg-neutral-900 border border-neutral-700 rounded-lg px-3 py-1.5 text-sm focus:border-indigo-500 focus:outline-none"
          >
            <option value="bge">bge-m3 (1024d)</option>
            <option value="openai">openai (1536d)</option>
          </select>

          {/* 결과 수 */}
          <div className="flex items-center gap-1.5">
            <span className="text-xs text-neutral-400">결과</span>
            <input
              type="number" min={1} max={50} value={k}
              onChange={(e) => setK(Number(e.target.value) || 10)}
              className="w-14 bg-neutral-900 border border-neutral-700 rounded-lg px-2 py-1.5 text-sm text-right focus:border-indigo-500 focus:outline-none"
            />
          </div>

          {/* LLM 토글 */}
          <label className="flex items-center gap-2 cursor-pointer select-none">
            <div
              onClick={() => setUseLlm((v) => !v)}
              className={`w-10 h-5 rounded-full transition relative ${useLlm ? "bg-indigo-600" : "bg-neutral-700"}`}
            >
              <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all ${useLlm ? "left-5" : "left-0.5"}`} />
            </div>
            <span className={`text-sm ${useLlm ? "text-indigo-400" : "text-neutral-400"}`}>🤖 LLM 변환</span>
          </label>

          {useLlm && (
            <select
              value={llmProvider}
              onChange={(e) => setLlmProvider(e.target.value as "groq" | "openai")}
              className="bg-neutral-900 border border-neutral-700 rounded-lg px-3 py-1.5 text-sm focus:border-indigo-500 focus:outline-none"
            >
              <option value="groq">Groq Llama-3.3</option>
              <option value="openai">GPT-4o-mini</option>
            </select>
          )}
        </div>

        {/* 검색 입력 */}
        <div className="flex gap-2">
          <input
            type="text"
            placeholder="자연어로 검색 (예: 봄 데이트에 어울리는 14k 귀걸이)"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") search(); }}
            className="flex-1 bg-neutral-900 border border-neutral-700 rounded-lg px-4 py-2.5 text-sm focus:border-indigo-500 focus:outline-none placeholder:text-neutral-600"
            disabled={loading}
          />
          <button
            onClick={() => search()}
            disabled={loading || !query.trim()}
            className="px-5 py-2.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:bg-neutral-800 disabled:text-neutral-600 text-sm font-medium transition"
          >
            {loading ? "검색 중..." : "검색"}
          </button>
        </div>

        {/* 예시 쿼리 */}
        {items.length === 0 && !loading && (
          <div className="flex flex-wrap gap-2">
            {SAMPLE_QUERIES.map((q) => (
              <button
                key={q}
                onClick={() => search(q)}
                className="px-3 py-1 text-xs rounded-full border border-neutral-700 text-neutral-400 hover:text-white hover:border-neutral-500 transition"
              >
                {q}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* LLM 해석 바 */}
      {useLlm && parsed && (
        <div className="px-6 py-2 border-b border-neutral-800 bg-indigo-950/30">
          <div className="flex items-center gap-2 flex-wrap text-xs">
            <span className="text-indigo-400 font-medium">🤖 LLM 해석</span>
            <span className="text-indigo-200 font-semibold">&quot;{parsed.semantic_query}&quot;</span>
            {debugInfo?.keyword_filter_relaxed && (
              <span className="px-1.5 py-0.5 bg-amber-900/50 text-amber-400 rounded border border-amber-800">⚠ 키워드필터 완화</span>
            )}
            {debugInfo?.pe_filter_relaxed && (
              <span className="px-1.5 py-0.5 bg-red-900/50 text-red-400 rounded border border-red-800">⚠ 정형필터 완화</span>
            )}
            {parsed.filters?.target_gender && (
              <span className="px-2 py-0.5 rounded-full bg-pink-900/50 text-pink-300 border border-pink-800">성별: {parsed.filters.target_gender}</span>
            )}
            {(parsed.filters?.target_age_min || parsed.filters?.target_age_max) && (
              <span className="px-2 py-0.5 rounded-full bg-blue-900/50 text-blue-300 border border-blue-800">
                연령: {parsed.filters.target_age_min ?? "?"}~{parsed.filters.target_age_max ?? "?"}대
              </span>
            )}
            {parsed.filters?.category_contains && (
              <span className="px-2 py-0.5 rounded-full bg-indigo-900/50 text-indigo-300 border border-indigo-800">카테고리: {parsed.filters.category_contains}</span>
            )}
            {parsed.filters?.perspective_preference && (
              <span className="px-2 py-0.5 rounded-full bg-purple-900/50 text-purple-300 border border-purple-800">관점: {parsed.filters.perspective_preference}</span>
            )}
            {(parsed.filters?.keyword_filters ?? []).map((kw) => (
              <span key={kw} className="px-2 py-0.5 rounded-full bg-orange-900/50 text-orange-300 border border-orange-800">키워드: {kw}</span>
            ))}
            {parsed.notes && <span className="text-neutral-500 italic ml-1">{parsed.notes}</span>}
          </div>
        </div>
      )}

      {/* 에러 */}
      {error && (
        <div className="mx-6 mt-3 p-3 rounded-lg bg-red-950 border border-red-800 text-red-300 text-sm">{error}</div>
      )}

      {/* 결과 */}
      <main className="flex-1 overflow-y-auto px-6 py-4">
        {loading && (
          <div className="text-center text-neutral-500 py-16 text-sm">
            <div className="inline-block w-2 h-2 rounded-full bg-indigo-400 animate-pulse mr-2" />
            {useLlm ? "LLM 분석 → 임베딩 → 벡터 검색 중..." : "임베딩 → 벡터 검색 중..."}
          </div>
        )}

        {!loading && items.length === 0 && !error && (
          <div className="text-center text-neutral-600 py-16 text-sm">검색어를 입력하세요</div>
        )}

        {!loading && items.length > 0 && (
          <div className="space-y-1.5">
            <div className="text-xs text-neutral-500 mb-3">{items.length}개 결과</div>
            {items.map((it, idx) => (
              <button
                key={`${it.rv_product_id}-${idx}`}
                onClick={() => setSelectedItem(selectedItem?.rv_product_id === it.rv_product_id ? null : it)}
                className={`w-full text-left rounded-lg border p-3 transition ${
                  selectedItem?.rv_product_id === it.rv_product_id
                    ? "bg-indigo-950/40 border-indigo-700"
                    : "bg-neutral-900 border-neutral-800 hover:border-neutral-600"
                }`}
              >
                <div className="flex gap-3">
                  {it.image_url
                    ? <img src={it.image_url} alt="" className="w-16 h-16 object-cover rounded-lg bg-neutral-800 shrink-0" />
                    : <div className="w-16 h-16 rounded-lg bg-neutral-800 shrink-0" />}
                  <div className="min-w-0 flex-1">
                    <div className="flex items-start justify-between gap-2">
                      <span className="font-medium text-sm truncate">{it.product_name}</span>
                      <span className="text-[10px] font-mono text-neutral-600 shrink-0">
                        dist {it.distance.toFixed(3)}
                      </span>
                    </div>

                    {/* 가격 */}
                    {(it.sale_price != null || it.price != null) && (
                      <div className="flex items-baseline gap-1.5 mt-0.5">
                        <span className="text-sm font-bold text-emerald-400">
                          {(it.sale_price ?? it.price)!.toLocaleString()}원
                        </span>
                        {it.price != null && it.sale_price != null && it.price !== it.sale_price && (
                          <>
                            <span className="text-xs text-neutral-600 line-through">{it.price.toLocaleString()}원</span>
                            <span className="text-[10px] text-red-400 font-medium">
                              {Math.round((1 - it.sale_price / it.price) * 100)}% 할인
                            </span>
                          </>
                        )}
                      </div>
                    )}

                    <div className="flex flex-wrap gap-1 mt-1">
                      <span className={`text-[10px] px-1.5 py-0.5 rounded font-mono ${
                        it.perspective === "situation" ? "bg-blue-900/60 text-blue-300" :
                        it.perspective === "material" ? "bg-teal-900/60 text-teal-300" :
                        it.perspective === "style" ? "bg-purple-900/60 text-purple-300" :
                        "bg-pink-900/60 text-pink-300"
                      }`}>
                        {PERSPECTIVE_LABEL[it.perspective] ?? it.perspective}
                      </span>
                      {it.category && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-indigo-900/50 text-indigo-300 border border-indigo-800/50">{it.category}</span>
                      )}
                      {it.brand && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-neutral-800 text-neutral-400">{it.brand}</span>
                      )}
                      {it.target_gender && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-neutral-800 text-neutral-400">{it.target_gender}</span>
                      )}
                    </div>

                    {selectedItem?.rv_product_id === it.rv_product_id && (
                      <p className="text-xs text-neutral-400 mt-2 leading-relaxed">{it.description}</p>
                    )}
                  </div>

                  <div className="shrink-0 self-center flex flex-col gap-1.5">
                    <button
                      onClick={(e) => { e.stopPropagation(); openDetail(it.rv_product_id); }}
                      className="text-xs px-2 py-1.5 rounded bg-indigo-900 hover:bg-indigo-800 text-indigo-300 transition"
                    >
                      상세
                    </button>
                    {it.product_url && (
                      <a
                        href={it.product_url} target="_blank" rel="noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="text-xs px-2 py-1.5 rounded bg-neutral-800 hover:bg-neutral-700 text-neutral-300 transition text-center"
                      >
                        보기
                      </a>
                    )}
                  </div>
                </div>
              </button>
            ))}
          </div>
        )}
      </main>
      {/* 상세 모달 */}
      {(detail || detailLoading) && (
        <div className="fixed inset-0 bg-black/70 z-50 flex items-center justify-center p-4" onClick={() => setDetail(null)}>
          <div className="bg-neutral-900 border border-neutral-700 rounded-xl w-full max-w-4xl max-h-[90vh] flex flex-col shadow-2xl" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between px-5 py-4 border-b border-neutral-800">
              <div>
                <div className="font-semibold text-base">{detail?.product_name ?? "로딩 중..."}</div>
                {detail && (
                  <div className="text-xs text-neutral-500 font-mono mt-0.5">
                    rv_id={detail.rv_product_id} · #{detail.product_code} · advertiser={detail.advertiser_id}
                    {detail.cost_usd != null && ` · $${detail.cost_usd.toFixed(4)}`}
                    {detail.duration_ms != null && ` · ${(detail.duration_ms / 1000).toFixed(1)}s`}
                  </div>
                )}
              </div>
              <button onClick={() => setDetail(null)} className="text-neutral-400 hover:text-white text-xl leading-none ml-4">✕</button>
            </div>
            <div className="overflow-y-auto flex-1 p-5 space-y-5 text-sm">
              {detailLoading && <div className="text-neutral-500 text-center py-12">로딩 중...</div>}
              {detail && (
                <>
                  {/* 이미지 + 기본 정보 */}
                  <div className="flex gap-4">
                    {detail.image_url && (
                      <img src={detail.image_url} alt="" className="w-32 h-32 object-cover rounded-lg bg-neutral-800 shrink-0" />
                    )}
                    <div className="space-y-2">
                      <div className="flex flex-wrap gap-1 text-xs">
                        {detail.category && <span className="px-2 py-0.5 rounded bg-indigo-900/50 text-indigo-300 border border-indigo-800">{detail.category}{detail.category_confidence != null && ` (${detail.category_confidence.toFixed(2)})`}</span>}
                        {detail.brand && <span className="px-2 py-0.5 rounded bg-purple-900/50 text-purple-300 border border-purple-800">{detail.brand}</span>}
                        {detail.brand_tier && <span className="px-2 py-0.5 rounded bg-amber-900/50 text-amber-300 border border-amber-800">{detail.brand_tier}</span>}
                        {detail.target_gender && <span className="px-2 py-0.5 rounded bg-pink-900/50 text-pink-300 border border-pink-800">{detail.target_gender}</span>}
                        {detail.target_age_min != null && detail.target_age_max != null && (
                          <span className="px-2 py-0.5 rounded bg-blue-900/50 text-blue-300 border border-blue-800">{detail.target_age_min}~{detail.target_age_max}대</span>
                        )}
                        {detail.origin_country && <span className="px-2 py-0.5 rounded bg-neutral-800 text-neutral-300">{detail.origin_country}</span>}
                      </div>
                      {detail.product_url && (
                        <a href={detail.product_url} target="_blank" rel="noreferrer" className="text-xs text-indigo-400 hover:underline">상품 페이지 →</a>
                      )}
                    </div>
                  </div>

                  {/* 4관점 설명문 */}
                  <div>
                    <div className="text-xs text-neutral-500 font-medium mb-2">다관점 설명문</div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                      {[
                        { p: "situation", label: "상황", t: detail.desc_situation, color: "border-blue-800/50 bg-blue-950/20" },
                        { p: "material",  label: "소재", t: detail.desc_material,  color: "border-teal-800/50 bg-teal-950/20" },
                        { p: "style",     label: "스타일", t: detail.desc_style,   color: "border-purple-800/50 bg-purple-950/20" },
                        { p: "persona",   label: "페르소나", t: detail.desc_persona, color: "border-pink-800/50 bg-pink-950/20" },
                      ].map(({ p, label, t, color }) => {
                        const m = detail.descriptions_meta?.find((x) => x.perspective === p);
                        return (
                          <div key={p} className={`border rounded-lg p-3 ${color}`}>
                            <div className="flex items-center justify-between text-xs mb-1">
                              <span className="font-medium text-neutral-300">{label}</span>
                              <span className="font-mono text-neutral-600">bge {m?.has_bge ? "✓" : "✗"}</span>
                            </div>
                            <p className="text-xs text-neutral-300 leading-relaxed">{t ?? <span className="text-neutral-600">없음</span>}</p>
                          </div>
                        );
                      })}
                    </div>
                  </div>

                  {/* 태그 */}
                  {detail.tags && detail.tags.length > 0 && (
                    <div>
                      <div className="text-xs text-neutral-500 font-medium mb-2">태그 ({detail.tags.length}개)</div>
                      <div className="flex flex-wrap gap-1">
                        {detail.tags.map((t, i) => (
                          <span key={i} className={`text-[11px] px-1.5 py-0.5 rounded font-mono ${
                            t.tag_category === "situation" ? "bg-blue-900/50 text-blue-300" :
                            t.tag_category === "mood" ? "bg-pink-900/50 text-pink-300" :
                            t.tag_category === "endorser" ? "bg-amber-900/50 text-amber-300" :
                            "bg-neutral-800 text-neutral-300"
                          }`}>{t.tag}</span>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* 카테고리 속성 */}
                  {detail.category_attributes && Object.keys(detail.category_attributes).length > 0 && (
                    <div>
                      <div className="text-xs text-neutral-500 font-medium mb-2">카테고리 속성</div>
                      <div className="bg-neutral-800 rounded-lg p-3 space-y-1">
                        {Object.entries(detail.category_attributes).map(([k, v]) => (
                          <div key={k} className="flex gap-2 text-xs">
                            <span className="text-neutral-500 w-28 shrink-0">{k}</span>
                            <span className="text-neutral-200">{String(v)}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* 이미지 분류 */}
                  {detail.image_types && detail.image_types.length > 0 && (
                    <div>
                      <div className="text-xs text-neutral-500 font-medium mb-2">이미지 분류 ({detail.image_types.length}장)</div>
                      <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 gap-2">
                        {detail.image_types.map((im) => {
                          const used = im.used_for_attributes && im.is_main_product;
                          return (
                            <div key={im.sha1} className={`border rounded-lg overflow-hidden text-[10px] ${used ? "border-emerald-700" : "border-neutral-700 opacity-60"}`}>
                              {/* eslint-disable-next-line @next/next/no-img-element */}
                              <img src={imgSrc(detail.advertiser_id, detail.product_code, im.sha1)} alt="" className="w-full h-20 object-cover bg-neutral-800" />
                              <div className="p-1 bg-neutral-900">
                                <div className="flex items-center justify-between">
                                  <span className="font-mono text-indigo-400">{im.image_type}</span>
                                  <span className={used ? "text-emerald-400" : "text-neutral-600"}>{used ? "✓" : "skip"}</span>
                                </div>
                                <p className="text-neutral-500 line-clamp-2" title={im.content_description}>{im.content_description}</p>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}

                  {/* set/호환 상품 */}
                  {detail.set_components && detail.set_components.length > 0 && (
                    <div>
                      <div className="text-xs text-neutral-500 font-medium mb-2">SET 구성</div>
                      <pre className="text-xs bg-neutral-800 rounded-lg p-3 overflow-x-auto">{JSON.stringify(detail.set_components, null, 2)}</pre>
                    </div>
                  )}
                  {detail.compatible_products && detail.compatible_products.length > 0 && (
                    <div>
                      <div className="text-xs text-neutral-500 font-medium mb-2">호환/관련 상품</div>
                      <pre className="text-xs bg-amber-950/30 border border-amber-900/50 rounded-lg p-3 overflow-x-auto">{JSON.stringify(detail.compatible_products, null, 2)}</pre>
                    </div>
                  )}

                  {/* 비용 정보 */}
                  <div className="text-xs text-neutral-600 font-mono border-t border-neutral-800 pt-3 flex flex-wrap gap-3">
                    <span>model: {detail.model_used ?? "-"}</span>
                    <span>in: {detail.input_tokens ?? 0} tokens</span>
                    <span>out: {detail.output_tokens ?? 0} tokens</span>
                    <span>cost: ${detail.cost_usd?.toFixed(4) ?? "?"}</span>
                    <span>enriched: {detail.enriched_at?.slice(0, 16) ?? "-"}</span>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
