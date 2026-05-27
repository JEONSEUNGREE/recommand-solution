"use client";

import { useEffect, useState, useCallback, useRef } from "react";

const EMBEDDER_BASE = process.env.NEXT_PUBLIC_EMBEDDER_BASE ?? "/api/embedder";

// ─── 공통 타입 ───────────────────────────────────────────────
type Tag = {
  tag?: string; name?: string; tag_category?: string; category?: string;
  confidence?: number; gender?: string; verified?: boolean;
};
type CategoryCount = { category: string; cnt: number };

type ProductItem = {
  rv_product_id: number; product_name: string; product_code: string;
  price: number | null; sale_price: number | null; image_url: string | null;
  product_url: string | null; category: string | null; brand: string | null;
  enrich_status: string; tags: Tag[] | null;
  desc_situation: string | null; desc_style: string | null;
  desc_persona: string | null; enriched_at: string | null;
};
type DetailRow = ProductItem & {
  desc_material: string | null; target_gender: string | null;
  target_age_min: number | null; target_age_max: number | null;
  category_attributes: Record<string, unknown> | null;
  images: string[]; descriptions: { perspective: string; description: string }[];
};
type ListResponse = { total: number; page: number; size: number; items: ProductItem[] };
type EndorserProduct = {
  rv_product_id: number; product_name: string; product_code: string;
  image_url: string | null; product_url: string | null;
  tags: Tag[] | null; desc_persona: string | null;
};
type SearchLog = {
  id: number; query: string; semantic_query: string | null;
  llm_used: boolean; llm_provider: string | null; backend: string;
  advertiser_id: number | null; result_count: number;
  keyword_filters: string[]; keyword_filter_relaxed: boolean;
  created_at: string;
};
type LogStats = {
  total: number; zero_results: number; llm_used: number;
  avg_results: number; active_days: number;
  top_zero_queries: { query: string; cnt: number }[];
};

const PAGE_SIZE = 20;
const GENDERS = ["여성", "남성", "혼성", "불명"] as const;

// ─── 루트 페이지 ─────────────────────────────────────────────
export default function ProductsPage() {
  const [tab, setTab] = useState<"list" | "endorser" | "logs">("list");
  const [count, setCount] = useState<{ total: number; embedded: number } | null>(null);

  useEffect(() => {
    fetch(`${EMBEDDER_BASE}/products-enriched/count`)
      .then((r) => r.json()).then(setCount).catch(console.error);
  }, []);

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100 flex flex-col">
      {/* 헤더 */}
      <header className="border-b border-neutral-800 px-6 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">상품 관리</h1>
          {count && (
            <p className="text-xs text-neutral-400 mt-0.5">
              전체 <span className="text-white font-medium">{count.total.toLocaleString()}</span>건 ·
              임베딩 완료 <span className="text-emerald-400 font-medium">{count.embedded.toLocaleString()}</span>건
            </p>
          )}
        </div>
        <a href="/" className="text-sm text-neutral-400 hover:text-white">← 홈</a>
      </header>

      {/* 탭 */}
      <div className="flex border-b border-neutral-800 px-6">
        {([["list", "상품목록"], ["endorser", "연예인 검수"], ["logs", "검색 로그"]] as const).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`px-4 py-2.5 text-sm font-medium border-b-2 transition -mb-px ${
              tab === key
                ? "border-indigo-500 text-indigo-400"
                : "border-transparent text-neutral-400 hover:text-white"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "list" ? <ProductListTab /> : tab === "endorser" ? <EndorserReviewTab /> : <SearchLogsTab />}
    </div>
  );
}

// ─── 탭 1: 상품목록 ───────────────────────────────────────────
function ProductListTab() {
  const [categories, setCategories] = useState<CategoryCount[]>([]);
  const [q, setQ] = useState("");
  const [code, setCode] = useState("");
  const [tag, setTag] = useState("");
  const [category, setCategory] = useState("");
  const [page, setPage] = useState(0);
  const [result, setResult] = useState<ListResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<DetailRow | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const searchRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    fetch(`${EMBEDDER_BASE}/products-enriched/categories`)
      .then((r) => r.json()).then(setCategories).catch(console.error);
  }, []);

  const fetchList = useCallback(async (newPage = 0) => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: String(newPage), size: String(PAGE_SIZE) });
      if (q.trim()) params.set("q", q.trim());
      if (code.trim()) params.set("code", code.trim());
      if (tag.trim()) params.set("tag", tag.trim());
      if (category) params.set("category", category);
      const res = await fetch(`${EMBEDDER_BASE}/products-enriched?${params}`);
      setResult(await res.json());
      setPage(newPage);
    } catch (e) { console.error(e); }
    finally { setLoading(false); }
  }, [q, code, tag, category]);

  useEffect(() => { fetchList(0); }, []); // eslint-disable-line

  useEffect(() => {
    if (searchRef.current) clearTimeout(searchRef.current);
    searchRef.current = setTimeout(() => fetchList(0), 400);
    return () => { if (searchRef.current) clearTimeout(searchRef.current); };
  }, [q, code, tag, category]); // eslint-disable-line

  async function openDetail(id: number) {
    setDetailLoading(true); setDetail(null);
    try {
      const res = await fetch(`${EMBEDDER_BASE}/products-enriched/${id}`);
      setDetail(await res.json());
    } catch (e) { console.error(e); }
    finally { setDetailLoading(false); }
  }

  const totalPages = result ? Math.ceil(result.total / PAGE_SIZE) : 0;

  return (
    <div className="flex flex-1 min-h-0">
      <div className="flex-1 flex flex-col min-w-0">
        {/* 검색 바 */}
        <div className="px-6 py-3 border-b border-neutral-800 flex flex-wrap gap-3">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="상품명 검색..."
            className="flex-1 min-w-48 px-3 py-2 rounded-lg bg-neutral-900 border border-neutral-700 focus:border-indigo-500 focus:outline-none text-sm" />
          <input value={code} onChange={(e) => setCode(e.target.value)} placeholder="상품코드..."
            className="w-44 px-3 py-2 rounded-lg bg-neutral-900 border border-neutral-700 focus:border-indigo-500 focus:outline-none text-sm" />
          <input value={tag} onChange={(e) => setTag(e.target.value)} placeholder="태그 (예: 골드)"
            className="w-36 px-3 py-2 rounded-lg bg-neutral-900 border border-neutral-700 focus:border-indigo-500 focus:outline-none text-sm" />
          <select value={category} onChange={(e) => setCategory(e.target.value)}
            className="w-52 px-3 py-2 rounded-lg bg-neutral-900 border border-neutral-700 focus:border-indigo-500 focus:outline-none text-sm">
            <option value="">전체 카테고리</option>
            {categories.map((c) => (
              <option key={c.category} value={c.category}>{c.category} ({c.cnt})</option>
            ))}
          </select>
          <button onClick={() => fetchList(0)} className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-sm">검색</button>
        </div>

        {result && (
          <div className="px-6 py-2 text-xs text-neutral-400">
            {loading ? "검색 중..." : `${result.total.toLocaleString()}건 중 ${page * PAGE_SIZE + 1}~${Math.min((page + 1) * PAGE_SIZE, result.total)}건`}
          </div>
        )}

        <div className="flex-1 overflow-y-auto px-6 pb-6">
          <div className="grid grid-cols-1 gap-2">
            {result?.items.map((p) => (
              <button key={p.rv_product_id} onClick={() => openDetail(p.rv_product_id)}
                className="w-full text-left bg-neutral-900 hover:bg-neutral-800 border border-neutral-800 rounded-lg p-3 transition">
                <div className="flex gap-3 items-start">
                  {p.image_url && (
                    <img src={p.image_url} alt="" className="w-14 h-14 object-cover rounded flex-shrink-0 bg-neutral-800"
                      onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }} />
                  )}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-start justify-between gap-2">
                      <div className="font-medium text-sm truncate">{p.product_name}</div>
                      <div className="flex-shrink-0 text-right">
                        {p.sale_price != null ? (
                          <span className="text-indigo-300 text-sm font-semibold">{Number(p.sale_price).toLocaleString()}원</span>
                        ) : p.price != null ? (
                          <span className="text-neutral-300 text-sm">{Number(p.price).toLocaleString()}원</span>
                        ) : null}
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-x-2 text-xs text-neutral-500 mt-0.5">
                      {p.category && <span>{p.category}</span>}
                      {p.brand && <><span>·</span><span>{p.brand}</span></>}
                      <span className={p.enrich_status === "embedded" ? "text-emerald-500" : "text-amber-500"}>{p.enrich_status}</span>
                    </div>
                    {p.tags && p.tags.length > 0 && (
                      <div className="flex flex-wrap gap-1 mt-1">
                        {p.tags.slice(0, 6).map((t, i) => (
                          <span key={i} className="px-1.5 py-0.5 bg-neutral-800 text-neutral-300 rounded text-xs">{t.tag ?? t.name}</span>
                        ))}
                        {p.tags.length > 6 && <span className="text-xs text-neutral-500">+{p.tags.length - 6}</span>}
                      </div>
                    )}
                    {p.desc_situation && <p className="text-xs text-neutral-400 mt-1 line-clamp-1">{p.desc_situation}</p>}
                  </div>
                </div>
              </button>
            ))}
          </div>

          {result && totalPages > 1 && (
            <div className="flex items-center justify-center gap-2 mt-6">
              <button onClick={() => fetchList(page - 1)} disabled={page === 0 || loading}
                className="px-3 py-1.5 text-sm rounded border border-neutral-700 disabled:opacity-30 hover:bg-neutral-800">이전</button>
              <span className="text-sm text-neutral-400">{page + 1} / {totalPages}</span>
              <button onClick={() => fetchList(page + 1)} disabled={page >= totalPages - 1 || loading}
                className="px-3 py-1.5 text-sm rounded border border-neutral-700 disabled:opacity-30 hover:bg-neutral-800">다음</button>
            </div>
          )}
        </div>
      </div>

      {(detail || detailLoading) && (
        <aside className="w-96 border-l border-neutral-800 flex flex-col overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-neutral-800">
            <h2 className="font-semibold text-sm">상품 상세</h2>
            <button onClick={() => setDetail(null)} className="text-neutral-400 hover:text-white text-lg leading-none">×</button>
          </div>
          <div className="flex-1 overflow-y-auto p-4 space-y-4 text-sm">
            {detailLoading && <div className="text-neutral-400 text-center mt-8">불러오는 중...</div>}
            {detail && <DetailPanel d={detail} />}
          </div>
        </aside>
      )}
    </div>
  );
}

// ─── 탭 2: 연예인 검수 ────────────────────────────────────────
function EndorserReviewTab() {
  const [items, setItems] = useState<EndorserProduct[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<"all" | "unverified" | "verified">("unverified");
  const [saving, setSaving] = useState<string | null>(null); // "rv_id:tag_name"

  useEffect(() => {
    fetch(`${EMBEDDER_BASE}/products-enriched/endorsers`)
      .then((r) => r.json()).then(setItems).catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  async function updateGender(rv_id: number, tag_name: string, gender: string, verified: boolean) {
    const key = `${rv_id}:${tag_name}`;
    setSaving(key);
    try {
      const r = await fetch(`${EMBEDDER_BASE}/products-enriched/${rv_id}/endorser-tag`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tag_name, gender, verified }),
      });
      if (!r.ok) throw new Error(await r.text());
      // 로컬 상태 업데이트
      setItems((prev) => prev.map((item) => {
        if (item.rv_product_id !== rv_id) return item;
        return {
          ...item,
          tags: (item.tags || []).map((t) =>
            t.tag_category === "endorser" && t.tag === tag_name
              ? { ...t, gender, verified }
              : t
          ),
        };
      }));
    } catch (e) {
      alert(`저장 실패: ${e}`);
    } finally {
      setSaving(null);
    }
  }

  const endorserItems = items.map((item) => ({
    ...item,
    endorserTags: (item.tags || []).filter((t) => t.tag_category === "endorser"),
  }));

  const filtered = endorserItems.filter((item) => {
    if (filter === "verified") return item.endorserTags.every((t) => t.verified);
    if (filter === "unverified") return item.endorserTags.some((t) => !t.verified);
    return true;
  });

  const verifiedCount = endorserItems.filter((item) => item.endorserTags.every((t) => t.verified)).length;

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* 안내 배너 */}
      <div className="mx-6 mt-4 p-3 rounded-lg bg-amber-950 border border-amber-800 text-amber-200 text-xs">
        <span className="font-semibold">⚠ 검수 필요</span> — LLM이 자동으로 연예인 성별을 분류했습니다.
        그룹 해체·재편·혼성 여부 등 오분류가 있을 수 있으니 한 번씩 확인해주세요.
        성별 수정 시 임베딩이 자동으로 재생성됩니다.
      </div>

      {/* 필터 + 카운터 */}
      <div className="px-6 py-3 flex items-center gap-3">
        <div className="flex gap-1">
          {([["all", "전체"], ["unverified", "미확인"], ["verified", "확인완료"]] as const).map(([key, label]) => (
            <button key={key} onClick={() => setFilter(key)}
              className={`px-3 py-1 rounded text-xs font-medium transition ${
                filter === key ? "bg-indigo-600 text-white" : "bg-neutral-800 text-neutral-400 hover:text-white"
              }`}>
              {label}
            </button>
          ))}
        </div>
        <span className="text-xs text-neutral-500">
          {verifiedCount}/{endorserItems.length}건 확인완료 · 표시 {filtered.length}건
        </span>
      </div>

      {/* 목록 */}
      <div className="flex-1 overflow-y-auto px-6 pb-6">
        {loading && <div className="text-neutral-400 text-sm text-center mt-12">불러오는 중...</div>}
        <div className="grid grid-cols-1 gap-3">
          {filtered.map((item) => {
            const allVerified = item.endorserTags.every((t) => t.verified);
            return (
              <div key={item.rv_product_id}
                className={`rounded-lg border p-4 ${allVerified ? "bg-neutral-900 border-emerald-900" : "bg-neutral-900 border-neutral-800"}`}>
                <div className="flex gap-3">
                  {item.image_url && (
                    <img src={item.image_url} alt="" className="w-16 h-16 object-cover rounded flex-shrink-0 bg-neutral-800"
                      onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }} />
                  )}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <span className="font-medium text-sm">{item.product_name}</span>
                        <span className="ml-2 text-xs text-neutral-500 font-mono">#{item.product_code}</span>
                      </div>
                      {allVerified && <span className="text-xs text-emerald-400 font-medium flex-shrink-0">✓ 확인완료</span>}
                    </div>

                    {/* desc_persona */}
                    {item.desc_persona && (
                      <p className="text-xs text-neutral-400 mt-1 line-clamp-1">{item.desc_persona}</p>
                    )}

                    {/* endorser 태그 목록 */}
                    <div className="mt-2 space-y-1.5">
                      {item.endorserTags.map((t) => {
                        const key = `${item.rv_product_id}:${t.tag}`;
                        const isSaving = saving === key;
                        const genderColor: Record<string, string> = {
                          "여성": "bg-pink-900 text-pink-200 border-pink-700",
                          "남성": "bg-blue-900 text-blue-200 border-blue-700",
                          "혼성": "bg-purple-900 text-purple-200 border-purple-700",
                          "불명": "bg-neutral-700 text-neutral-300 border-neutral-600",
                        };
                        const cls = genderColor[t.gender ?? "불명"] ?? genderColor["불명"];
                        return (
                          <div key={t.tag} className="flex items-center gap-2 flex-wrap">
                            <span className="text-sm font-medium text-neutral-200 w-24 truncate">{t.tag}</span>
                            {/* gender 선택 */}
                            <div className="flex gap-1">
                              {GENDERS.map((g) => (
                                <button key={g} disabled={isSaving}
                                  onClick={() => updateGender(item.rv_product_id, t.tag!, g, t.verified ?? false)}
                                  className={`px-2 py-0.5 rounded border text-xs transition ${
                                    t.gender === g
                                      ? cls
                                      : "bg-neutral-800 text-neutral-500 border-neutral-700 hover:text-white"
                                  }`}>
                                  {g}
                                </button>
                              ))}
                            </div>
                            {/* 확인 버튼 */}
                            <button disabled={isSaving}
                              onClick={() => updateGender(item.rv_product_id, t.tag!, t.gender ?? "불명", !t.verified)}
                              className={`px-2 py-0.5 rounded text-xs font-medium transition ${
                                t.verified
                                  ? "bg-emerald-900 text-emerald-300 hover:bg-emerald-800"
                                  : "bg-neutral-800 text-neutral-400 hover:bg-neutral-700 hover:text-white"
                              }`}>
                              {isSaving ? "저장중..." : t.verified ? "✓ 확인됨" : "확인"}
                            </button>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
          {!loading && filtered.length === 0 && (
            <div className="text-center text-neutral-500 py-12 text-sm">
              {filter === "verified" ? "아직 확인완료된 항목이 없습니다." : "모든 항목이 확인완료되었습니다! 🎉"}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── 상세 패널 ────────────────────────────────────────────────
function DetailPanel({ d }: { d: DetailRow }) {
  const price = d.sale_price ?? d.price;
  const perspectiveLabel: Record<string, string> = {
    situation: "상황", material: "소재", style: "스타일", persona: "페르소나",
  };
  return (
    <div className="space-y-4">
      {d.image_url && (
        <img src={d.image_url} alt={d.product_name}
          className="w-full rounded-lg object-cover max-h-64 bg-neutral-800"
          onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }} />
      )}
      <div>
        <h3 className="font-semibold text-base">{d.product_name}</h3>
        <div className="flex items-center gap-2 mt-1">
          {price && <span className="text-indigo-300 font-semibold">{Number(price).toLocaleString()}원</span>}
          {d.sale_price && d.price && Number(d.price) > Number(d.sale_price) && (
            <span className="text-neutral-500 line-through text-xs">{Number(d.price).toLocaleString()}원</span>
          )}
        </div>
        <div className="flex flex-wrap gap-x-2 text-xs text_neutral-400 mt-1">
          {d.category && <span>{d.category}</span>}
          {d.brand && <><span>·</span><span>{d.brand}</span></>}
          {d.target_gender && <><span>·</span><span>{d.target_gender}</span></>}
          {(d.target_age_min || d.target_age_max) && (
            <><span>·</span><span>{d.target_age_min ?? "?"}~{d.target_age_max ?? "?"}세</span></>
          )}
        </div>
        <div className="flex gap-2 mt-2">
          <span className={`text-xs px-2 py-0.5 rounded-full ${d.enrich_status === "embedded" ? "bg-emerald-900 text-emerald-300" : "bg-amber-900 text-amber-300"}`}>
            {d.enrich_status}
          </span>
        </div>
        {d.product_url && (
          <a href={d.product_url} target="_blank" rel="noreferrer"
            className="text-xs text-indigo-400 hover:underline mt-1 inline-block">상품 페이지 →</a>
        )}
      </div>

      {d.descriptions && d.descriptions.length > 0 && (
        <div>
          <div className="text-xs text-neutral-500 mb-2 font-medium">임베딩 설명문</div>
          <div className="space-y-2">
            {d.descriptions.map((desc) => (
              <div key={desc.perspective} className="bg-neutral-800 rounded px-3 py-2">
                <div className="text-xs text-neutral-500 mb-0.5">{perspectiveLabel[desc.perspective] ?? desc.perspective}</div>
                <p className="text-xs text-neutral-200">{desc.description}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {d.tags && d.tags.length > 0 && (
        <div>
          <div className="text-xs text-neutral-500 mb-2 font-medium">태그 ({d.tags.length}개)</div>
          <div className="flex flex-wrap gap-1">
            {d.tags.map((t, i) => {
              const cat = t.tag_category ?? t.category ?? "";
              const colorMap: Record<string, string> = {
                situation: "bg-blue-900 text-blue-200",
                mood: "bg-purple-900 text-purple-200",
                feature: "bg-teal-900 text-teal-200",
                endorser: "bg-pink-900 text-pink-200",
              };
              return (
                <span key={i} className={`px-1.5 py-0.5 rounded text-xs ${colorMap[cat] ?? "bg-neutral-800 text-neutral-300"}`}>
                  {t.tag ?? t.name}
                  {cat === "endorser" && t.gender && <span className="opacity-60 ml-0.5">({t.gender})</span>}
                </span>
              );
            })}
          </div>
        </div>
      )}

      {d.category_attributes && Object.keys(d.category_attributes).length > 0 && (
        <div>
          <div className="text-xs text-neutral-500 mb-2 font-medium">카테고리 속성</div>
          <div className="space-y-1">
            {Object.entries(d.category_attributes).map(([k, v]) => (
              <div key={k} className="flex gap-2 text-xs">
                <span className="text-neutral-500 w-24 flex-shrink-0">{k}</span>
                <span className="text-neutral-300">{String(v)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── 탭 3: 검색 로그 ─────────────────────────────────────────
const LOG_PAGE_SIZE = 50;

type LogResultItem = {
  rank: number; rv_product_id: number; product_name: string; product_code: string;
  image_url: string | null; product_url: string | null;
  price: number | null; sale_price: number | null;
  category: string | null; brand: string | null;
  distance: number; perspective: string; description: string | null;
  desc_persona: string | null;
};

function LogResultModal({ log, onClose }: { log: SearchLog; onClose: () => void }) {
  const [items, setItems] = useState<LogResultItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const run = async () => {
      try {
        const r = await fetch(`${EMBEDDER_BASE}/search-logs/${log.id}/results`);
        const data = await r.json();
        if (!r.ok) throw new Error(data.detail ?? `HTTP ${r.status}`);
        setItems(data.items ?? []);
      } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
      finally { setLoading(false); }
    };
    run();
  }, [log]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70"
      onClick={onClose}
    >
      <div
        className="bg-neutral-900 border border-neutral-700 rounded-xl w-full max-w-3xl max-h-[85vh] flex flex-col shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 헤더 */}
        <div className="flex items-start justify-between px-5 py-4 border-b border-neutral-800">
          <div>
            <div className="text-white font-semibold text-base">&quot;{log.query}&quot;</div>
            <div className="flex items-center gap-2 mt-1 flex-wrap">
              {log.semantic_query && log.semantic_query !== log.query && (
                <span className="text-xs text-indigo-400">→ &quot;{log.semantic_query}&quot;</span>
              )}
              {log.llm_used && (
                <span className="text-xs bg-indigo-900 text-indigo-300 rounded px-1.5 py-0.5">{log.llm_provider ?? "LLM"}</span>
              )}
              <span className="text-xs text-neutral-500">
                {new Date(log.created_at).toLocaleString("ko-KR")}
              </span>
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-neutral-400 hover:text-white text-xl leading-none ml-4 shrink-0"
          >✕</button>
        </div>

        {/* 바디 */}
        <div className="overflow-y-auto flex-1 p-4 space-y-2">
          {loading && (
            <div className="text-center text-neutral-500 py-16">불러오는 중...</div>
          )}
          {error && (
            <div className="text-center text-red-400 py-8 text-sm">{error}</div>
          )}
          {!loading && !error && items.length === 0 && (
            <div className="text-center text-neutral-500 py-16">결과 없음</div>
          )}
          {items.map((it) => (
            <div key={it.rank} className="flex gap-3 p-3 bg-neutral-800/50 rounded-lg border border-neutral-700/50 hover:border-neutral-600 transition">
              <div className="text-xs text-neutral-600 self-center w-4 text-center shrink-0">{it.rank}</div>
              {it.image_url
                ? <img src={it.image_url} alt="" className="w-14 h-14 rounded-lg object-cover bg-neutral-700 shrink-0" />
                : <div className="w-14 h-14 rounded-lg bg-neutral-700 shrink-0" />}
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-sm font-medium text-white truncate">{it.product_name}</span>
                  <span className="text-[10px] font-mono text-neutral-500">dist {it.distance.toFixed(3)}</span>
                  <span className="text-[10px] px-1 py-0.5 rounded bg-neutral-700 text-neutral-400">{it.perspective}</span>
                </div>
                {(it.sale_price != null || it.price != null) && (
                  <div className="flex items-baseline gap-1.5 mt-0.5">
                    <span className="text-sm font-bold text-emerald-400">
                      {(it.sale_price ?? it.price)!.toLocaleString()}원
                    </span>
                    {it.price != null && it.sale_price != null && it.price !== it.sale_price && (
                      <span className="text-xs text-neutral-500 line-through">{it.price.toLocaleString()}원</span>
                    )}
                  </div>
                )}
                <div className="flex gap-1 mt-1 flex-wrap">
                  {it.category && (
                    <span className="text-[10px] bg-indigo-950 text-indigo-300 border border-indigo-800 rounded px-1.5 py-0.5">
                      {it.category}
                    </span>
                  )}
                  {it.brand && (
                    <span className="text-[10px] bg-neutral-700 text-neutral-300 rounded px-1.5 py-0.5">{it.brand}</span>
                  )}
                </div>
                {it.desc_persona && (
                  <p className="text-[11px] text-neutral-500 mt-1 line-clamp-1">{it.desc_persona}</p>
                )}
              </div>
              {it.product_url && (
                <a
                  href={it.product_url} target="_blank" rel="noreferrer"
                  className="shrink-0 self-center text-xs px-2 py-1.5 rounded bg-neutral-700 hover:bg-neutral-600 text-neutral-300"
                  onClick={(e) => e.stopPropagation()}
                >
                  보기
                </a>
              )}
            </div>
          ))}
        </div>

        {/* 푸터 */}
        {!loading && (
          <div className="px-5 py-3 border-t border-neutral-800 text-xs text-neutral-500">
            저장된 결과 {items.length}개 / 로그 기록: {log.result_count}개
          </div>
        )}
      </div>
    </div>
  );
}

function SearchLogsTab() {
  const [logs, setLogs] = useState<SearchLog[]>([]);
  const [stats, setStats] = useState<LogStats | null>(null);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(false);
  const [zeroOnly, setZeroOnly] = useState(false);
  const [llmOnly, setLlmOnly] = useState(false);
  const [q, setQ] = useState("");
  const [showTopZero, setShowTopZero] = useState(false);
  const [selectedLog, setSelectedLog] = useState<SearchLog | null>(null);

  const fetchLogs = useCallback(async (newPage = 0) => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: String(newPage), size: String(LOG_PAGE_SIZE) });
      if (zeroOnly) params.set("zero_only", "true");
      if (llmOnly) params.set("llm_only", "true");
      if (q.trim()) params.set("q", q.trim());
      const r = await fetch(`${EMBEDDER_BASE}/search-logs?${params}`);
      if (!r.ok) return;
      const data = await r.json();
      setLogs(data.items ?? []);
      setTotal(data.total ?? 0);
      setPage(newPage);
    } catch (e) { console.error(e); }
    finally { setLoading(false); }
  }, [zeroOnly, llmOnly, q]);

  useEffect(() => {
    fetchLogs(0);
    fetch(`${EMBEDDER_BASE}/search-logs/stats`)
      .then((r) => r.ok ? r.json() : null)
      .then((data) => { if (data && typeof data.total === "number") setStats(data); })
      .catch(console.error);
  }, [fetchLogs]);

  const totalPages = Math.ceil(total / LOG_PAGE_SIZE);

  return (
    <div className="flex-1 overflow-auto p-6 space-y-4">
      {/* 통계 카드 */}
      {stats && (
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
          {[
            { label: "전체 검색", value: (stats.total ?? 0).toLocaleString() },
            { label: "결과 0건", value: (stats.zero_results ?? 0).toLocaleString(), red: (stats.zero_results ?? 0) > 0 },
            { label: "LLM 사용", value: (stats.llm_used ?? 0).toLocaleString() },
            { label: "평균 결과수", value: stats.avg_results != null ? String(stats.avg_results) : "—" },
            { label: "활성 일수", value: `${stats.active_days ?? 0}일` },
          ].map(({ label, value, red }) => (
            <div key={label} className="bg-neutral-900 rounded-lg p-3 border border-neutral-800">
              <div className="text-xs text-neutral-500">{label}</div>
              <div className={`text-xl font-bold mt-1 ${red ? "text-red-400" : "text-white"}`}>{value}</div>
            </div>
          ))}
        </div>
      )}

      {/* 자주 실패한 검색어 */}
      {stats && stats.top_zero_queries.length > 0 && (
        <div className="bg-neutral-900 border border-neutral-800 rounded-lg">
          <button
            onClick={() => setShowTopZero(!showTopZero)}
            className="w-full flex items-center justify-between px-4 py-3 text-sm text-neutral-300 hover:text-white"
          >
            <span>결과 0건 상위 검색어 ({stats.top_zero_queries.length}개)</span>
            <span className="text-neutral-500">{showTopZero ? "▲" : "▼"}</span>
          </button>
          {showTopZero && (
            <div className="px-4 pb-3 flex flex-wrap gap-2">
              {stats.top_zero_queries.map(({ query, cnt }) => (
                <span key={query} className="text-xs bg-red-950 text-red-300 border border-red-800 rounded px-2 py-1">
                  {query} <span className="text-red-500 ml-1">×{cnt}</span>
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* 필터 */}
      <div className="flex flex-wrap gap-2 items-center">
        <input
          className="bg-neutral-800 border border-neutral-700 rounded px-3 py-1.5 text-sm text-white w-56 focus:outline-none focus:border-indigo-500"
          placeholder="검색어 필터..."
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && fetchLogs(0)}
        />
        <label className="flex items-center gap-1.5 text-sm text-neutral-300 cursor-pointer select-none">
          <input type="checkbox" checked={zeroOnly} onChange={(e) => setZeroOnly(e.target.checked)} className="accent-red-500" />
          결과 0건만
        </label>
        <label className="flex items-center gap-1.5 text-sm text-neutral-300 cursor-pointer select-none">
          <input type="checkbox" checked={llmOnly} onChange={(e) => setLlmOnly(e.target.checked)} className="accent-indigo-500" />
          LLM 사용만
        </label>
        <button
          onClick={() => fetchLogs(0)}
          className="px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white text-sm rounded"
        >
          조회
        </button>
        <span className="text-xs text-neutral-500 ml-auto">{total.toLocaleString()}건</span>
      </div>

      {/* 로그 테이블 */}
      {loading ? (
        <div className="text-center text-neutral-500 py-12">로딩 중...</div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-neutral-800">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-neutral-800 text-neutral-400 text-xs">
                <th className="text-left px-3 py-2 font-medium">시간</th>
                <th className="text-left px-3 py-2 font-medium">검색어</th>
                <th className="text-left px-3 py-2 font-medium">정제 쿼리</th>
                <th className="text-center px-3 py-2 font-medium">LLM</th>
                <th className="text-center px-3 py-2 font-medium">결과수</th>
                <th className="text-left px-3 py-2 font-medium">키워드 필터</th>
              </tr>
            </thead>
            <tbody>
              {logs.map((log) => (
                <tr
                  key={log.id}
                  onClick={() => setSelectedLog(log)}
                  className={`border-b border-neutral-800/60 hover:bg-neutral-700/30 cursor-pointer ${
                    log.result_count === 0 ? "bg-red-950/20" : ""
                  }`}
                >
                  <td className="px-3 py-2 text-neutral-500 whitespace-nowrap text-xs">
                    {new Date(log.created_at).toLocaleString("ko-KR", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}
                  </td>
                  <td className="px-3 py-2 text-white max-w-[180px] truncate" title={log.query}>
                    {log.query}
                  </td>
                  <td className="px-3 py-2 text-neutral-400 max-w-[180px] truncate" title={log.semantic_query ?? ""}>
                    {log.semantic_query && log.semantic_query !== log.query ? log.semantic_query : <span className="text-neutral-700">—</span>}
                  </td>
                  <td className="px-3 py-2 text-center">
                    {log.llm_used
                      ? <span className="text-xs bg-indigo-900 text-indigo-300 rounded px-1.5 py-0.5">{log.llm_provider ?? "LLM"}</span>
                      : <span className="text-neutral-700 text-xs">—</span>}
                  </td>
                  <td className="px-3 py-2 text-center">
                    <span className={`font-medium ${log.result_count === 0 ? "text-red-400" : "text-emerald-400"}`}>
                      {log.result_count}
                    </span>
                    {log.keyword_filter_relaxed && (
                      <span className="ml-1 text-xs text-yellow-600" title="키워드 필터 완화됨">⚠</span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex flex-wrap gap-1">
                      {(log.keyword_filters ?? []).map((kw) => (
                        <span key={kw} className="text-xs bg-neutral-800 text-neutral-400 rounded px-1.5 py-0.5">{kw}</span>
                      ))}
                    </div>
                  </td>
                </tr>
              ))}
              {logs.length === 0 && (
                <tr>
                  <td colSpan={6} className="text-center text-neutral-500 py-10">로그 없음</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* 페이지네이션 */}
      {totalPages > 1 && (
        <div className="flex gap-2 justify-center pt-2">
          <button disabled={page === 0} onClick={() => fetchLogs(page - 1)}
            className="px-3 py-1.5 text-sm rounded bg-neutral-800 hover:bg-neutral-700 disabled:opacity-30">
            ← 이전
          </button>
          <span className="text-sm text-neutral-400 px-3 py-1.5">{page + 1} / {totalPages}</span>
          <button disabled={page >= totalPages - 1} onClick={() => fetchLogs(page + 1)}
            className="px-3 py-1.5 text-sm rounded bg-neutral-800 hover:bg-neutral-700 disabled:opacity-30">
            다음 →
          </button>
        </div>
      )}

      {selectedLog && (
        <LogResultModal log={selectedLog} onClose={() => setSelectedLog(null)} />
      )}
    </div>
  );
}
