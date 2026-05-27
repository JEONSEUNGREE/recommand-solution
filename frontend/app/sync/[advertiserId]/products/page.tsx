"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://192.168.101.27:8090";
const EMBEDDER_BASE = process.env.NEXT_PUBLIC_EMBEDDER_BASE ?? "/api/embedder";

type EnrichedDetail = {
  rv_product_id: number; advertiser_id: number; product_code: string;
  product_name: string; product_url: string; image_url: string | null;
  category: string | null; category_confidence: number | null;
  brand: string | null; brand_tier: string | null; price_tier: string | null;
  target_gender: string | null; target_age_min: number | null; target_age_max: number | null;
  origin_country: string | null; manufacturer: string | null;
  category_attributes: Record<string, unknown> | null;
  compatible_products: unknown[] | null;
  set_components: unknown[] | null;
  tags: { tag: string; tag_category: string; confidence?: number }[] | null;
  desc_situation: string | null; desc_material: string | null;
  desc_style: string | null; desc_persona: string | null;
  image_types: { sha1: string; image_type: string; content_description: string; is_main_product: boolean; used_for_attributes: boolean }[] | null;
  model_used: string | null; input_tokens: number | null; output_tokens: number | null;
  cost_usd: number | null; duration_ms: number | null; enriched_at: string | null;
  descriptions_meta: { perspective: string; desc_len: number; has_bge: boolean; has_openai: boolean }[];
};

type RvProduct = {
  id: number;
  advertiserId: number;
  productCode: string;
  productName: string;
  productUrl: string;
  imageUrl: string | null;
  bodyText: string | null;
  bodyTextLen: number | null;
  imageLocalCount: number | null;
  scrapeStatus: string | null;
  scrapedAt: string | null;
  enrichMethod: string | null;
  enrichedInfo: string | null;
  enrichedAt: string | null;
  enrichError: string | null;
};

type Counts = {
  total: number;
  pending: number;
  scraped_pending_enrich: number;
  done: number;
  llm_enriched?: number;
  vectorized?: number;
};

type Page = {
  page: number;
  size: number;
  total: number;
  filter: string;
  items: RvProduct[];
  counts?: Counts;
  enrichedIds?: number[];
  embeddedIds?: number[];
};

const FILTERS = [
  { value: "", label: "전체" },
  { value: "to_scrape", label: "미스크랩" },
  { value: "to_enrich", label: "스크랩됨/미정제" },
  { value: "done", label: "정제 완료" },
];

type ImageMeta = { filename: string; size: number; excluded: boolean; src: string };
type Block = { id: number; pattern: string; enabled: boolean };

type BatchStatus = {
  status: "IDLE" | "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED";
  total: number;
  done: number;
  ok: number;
  fail: number;
  currentCode: string;
  elapsedMs: number;
  lastError: string;
};

export default function ProductsPage() {
  const params = useParams<{ advertiserId: string }>();
  const advertiserId = Number(params.advertiserId);

  const [data, setData] = useState<Page | null>(null);
  const [filter, setFilter] = useState("");
  const [page, setPage] = useState(0);
  const [size] = useState(20);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, unknown>>({});

  // 탭 — 크롤링(scrape) vs 멀티모달 정제(enrich) vs 공통 이미지 필터
  const [activeTab, setActiveTab] = useState<"scrape" | "enrich" | "images">("scrape");

  // 공통 이미지 탭
  type CommonImage = { src_url: string; product_count: number };
  const [commonImages, setCommonImages] = useState<CommonImage[] | null>(null);
  const [commonImagesLoading, setCommonImagesLoading] = useState(false);
  const [selectedCommonImage, setSelectedCommonImage] = useState<CommonImage | null>(null);
  const [commonMinProducts, setCommonMinProducts] = useState(3);
  const [commonViewMode, setCommonViewMode] = useState<"list" | "grid">("list");
  const [commonFilter, setCommonFilter] = useState<"all" | "blocked" | "unblocked">("all");
  const [checkedSrcs, setCheckedSrcs] = useState<Set<string>>(new Set());
  type ImageProducts = { product_code: string; product_name: string; product_url: string }[];
  const [popupProducts, setPopupProducts] = useState<ImageProducts | null>(null);
  type BlockEntry = { id: number; pattern: string; enabled: boolean };
  const [blockPatterns, setBlockPatterns] = useState<BlockEntry[]>([]);

  async function loadBlockPatterns() {
    const res = await fetch(`${EMBEDDER_BASE}/enrich-llm/image-blocks/${advertiserId}`);
    if (res.ok) setBlockPatterns(await res.json());
  }

  function isBlocked(src_url: string): boolean {
    return blockPatterns.some((b) => b.enabled && src_url.includes(b.pattern));
  }

  function getBlockEntry(src_url: string): BlockEntry | undefined {
    return blockPatterns.find((b) => b.enabled && src_url.includes(b.pattern));
  }

  async function removeImageBlock(blockId: number) {
    await fetch(`${EMBEDDER_BASE}/enrich-llm/image-blocks/${advertiserId}/${blockId}`, { method: "DELETE" });
    await loadBlockPatterns();
  }

  async function openGridPopup(img: { src_url: string; product_count: number }) {
    setSelectedCommonImage(img);
    setPopupProducts(null);
    const res = await fetch(`${EMBEDDER_BASE}/enrich-llm/image-products/${advertiserId}?src_url=${encodeURIComponent(img.src_url)}&limit=5`);
    if (res.ok) setPopupProducts(await res.json());
  }

  async function addImageBlock(pattern: string) {
    await fetch(`${EMBEDDER_BASE}/enrich-llm/image-blocks/${advertiserId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ patterns: [pattern] }),
    });
  }

  async function bulkAddImageBlocks(srcs: string[]) {
    if (!srcs.length) return;
    if (!confirm(`선택한 ${srcs.length}개 이미지 URL을 차단 패턴에 추가할까요?\n이후 enrich 시 이 이미지들은 자동 제외됩니다.`)) return;
    const res = await fetch(`${EMBEDDER_BASE}/enrich-llm/image-blocks/${advertiserId}/batch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ patterns: srcs }),
    });
    const data = await res.json() as { added?: number };
    setCheckedSrcs(new Set());
    await loadBlockPatterns();
    alert(`${data.added ?? srcs.length}개 차단 패턴 등록 완료`);
  }

  async function loadCommonImages(minProducts = commonMinProducts) {
    setCommonImagesLoading(true);
    try {
      const res = await fetch(`${EMBEDDER_BASE}/enrich-llm/common-images/${advertiserId}?min_products=${minProducts}&limit=300`);
      if (res.ok) setCommonImages(await res.json());
    } finally {
      setCommonImagesLoading(false);
    }
  }

  useEffect(() => {
    if (activeTab === "images") {
      if (commonImages === null) loadCommonImages();
      loadBlockPatterns();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab]);

  // 배치 진행상황 (각각 별도)
  const [batch, setBatch] = useState<BatchStatus | null>(null);          // scrape
  const [enrichBatch, setEnrichBatch] = useState<BatchStatus | null>(null); // enrich

  // 두 배치 status polling (동시 진행 가능)
  useEffect(() => {
    let stopped = false;
    async function poll() {
      try {
        const [rs, re] = await Promise.all([
          fetch(`${API_BASE}/advertisers/${advertiserId}/products/batch-fetch/status`),
          fetch(`${API_BASE}/advertisers/${advertiserId}/products/batch-enrich/status`),
        ]);
        if (rs.ok) {
          const s = await rs.json() as BatchStatus;
          setBatch(s);
          if (s.status === "RUNNING" && !stopped) load();
        }
        if (re.ok) {
          const s = await re.json() as BatchStatus;
          setEnrichBatch(s);
          if (s.status === "RUNNING" && !stopped) load();
        }
      } catch {}
    }
    poll();
    const id = setInterval(poll, 2000);
    return () => { stopped = true; clearInterval(id); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [advertiserId]);

  // 스크랩 배치 설정
  const [batchCount, setBatchCount] = useState(500);
  const [batchConcurrency, setBatchConcurrency] = useState(4);
  const [batchIntervalMs, setBatchIntervalMs] = useState(1000);
  const [batchMode, setBatchMode] = useState<"api" | "page">("api");
  const [batchSize, setBatchSize] = useState(100);

  // 멀티모달 enrich 배치 설정
  const [enrichCount, setEnrichCount] = useState(20);
  const [enrichModel, setEnrichModel] = useState<"haiku" | "sonnet" | "opus">("haiku");
  const [enrichImageLimit, setEnrichImageLimit] = useState(10);
  const [enrichConcurrency, setEnrichConcurrency] = useState(2);
  const [enrichIntervalMs, setEnrichIntervalMs] = useState(0);
  const [enrichEmbedBackend, setEnrichEmbedBackend] = useState<"bge" | "openai" | "both">("bge");

  async function startEnrichBatch() {
    const perProduct = enrichModel === "haiku" ? 40 : enrichModel === "sonnet" ? 120 : 180;
    const estTotalMin = Math.round((enrichCount * perProduct) / (60 * enrichConcurrency));
    if (!confirm(
      `Enrich+Embed 대상 ${enrichCount}개 (벡터화 안 된 것부터)\n` +
      `LLM model=${enrichModel} · 이미지 ${enrichImageLimit}장 · embed=${enrichEmbedBackend}\n` +
      `워커 ${enrichConcurrency} · sleep ${enrichIntervalMs}ms\n` +
      `예상 약 ${estTotalMin}분`,
    )) return;
    const params = new URLSearchParams({
      count: String(enrichCount),
      model: enrichModel,
      imageLimit: String(enrichImageLimit),
      concurrency: String(enrichConcurrency),
      intervalMs: String(enrichIntervalMs),
      embedBackend: enrichEmbedBackend,
    });
    await fetch(`${API_BASE}/advertisers/${advertiserId}/products/batch-enrich?${params}`, { method: "POST" });
  }
  async function cancelEnrichBatch() {
    if (!confirm("진행 중인 enrich 배치를 취소할까요?")) return;
    await fetch(`${API_BASE}/advertisers/${advertiserId}/products/batch-enrich/cancel`, { method: "POST" });
  }

  async function startBatch() {
    const perProduct = batchMode === "api"
      ? 0.3   // api 모드는 batch당 0.3초 + 이미지 다운
      : 8 / batchConcurrency + batchIntervalMs / 1000;
    const estTotalMin = Math.round((batchCount * perProduct) / 60);
    if (!confirm(
      `미스크랩 상품 ${batchCount}개 시작?\n` +
      `mode=${batchMode}${batchMode === "api" ? `, 묶음 ${batchSize}` : ""}\n` +
      `워커 ${batchConcurrency}개 · sleep ${batchIntervalMs}ms\n` +
      `예상 약 ${estTotalMin}분`,
    )) return;
    const params = new URLSearchParams({
      count: String(batchCount),
      intervalMs: String(batchIntervalMs),
      concurrency: String(batchConcurrency),
      mode: batchMode,
      batchSize: String(batchSize),
    });
    await fetch(`${API_BASE}/advertisers/${advertiserId}/products/batch-fetch?${params}`, { method: "POST" });
  }
  async function cancelBatch() {
    if (!confirm("진행 중인 배치를 취소할까요? (현재 처리 중인 상품 끝나면 멈춤)")) return;
    await fetch(`${API_BASE}/advertisers/${advertiserId}/products/batch-fetch/cancel`, { method: "POST" });
  }

  // Enriched 상세 모달 (벡터 완료/LLM 완료 상품)
  const [enrichedDetail, setEnrichedDetail] = useState<EnrichedDetail | null>(null);
  const [enrichedDetailLoading, setEnrichedDetailLoading] = useState(false);

  async function openEnrichedDetail(rvId: number) {
    setEnrichedDetail(null); setEnrichedDetailLoading(true);
    try {
      const r = await fetch(`${EMBEDDER_BASE}/products-rv/${rvId}/detail`);
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || `HTTP ${r.status}`);
      setEnrichedDetail(j);
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    } finally {
      setEnrichedDetailLoading(false);
    }
  }

  function imgSrcFor(adv: number, code: string, sha1: string) {
    return `${API_BASE}/advertisers/${adv}/products/${code}/images/${sha1}.jpg`;
  }

  // 이미지 모달
  const [modalProduct, setModalProduct] = useState<RvProduct | null>(null);
  const [modalImages, setModalImages] = useState<ImageMeta[]>([]);
  const [modalBlocks, setModalBlocks] = useState<Block[]>([]);
  const [modalLoading, setModalLoading] = useState(false);

  async function reloadBlocks() {
    try {
      const rb = await fetch(`${API_BASE}/advertisers/${advertiserId}/image-blocks`);
      if (rb.ok) setModalBlocks(await rb.json());
    } catch {}
  }

  async function openImages(p: RvProduct) {
    setModalProduct(p);
    setModalImages([]);
    setModalBlocks([]);
    setModalLoading(true);
    try {
      const [r, rb] = await Promise.all([
        fetch(`${API_BASE}/advertisers/${advertiserId}/products/${p.productCode}/images`),
        fetch(`${API_BASE}/advertisers/${advertiserId}/image-blocks`),
      ]);
      if (r.ok) setModalImages(await r.json());
      if (rb.ok) setModalBlocks(await rb.json());
    } finally {
      setModalLoading(false);
    }
  }
  function closeModal() {
    setModalProduct(null);
    setModalImages([]);
    setModalBlocks([]);
  }

  /** 이미지 src가 enabled block 중 매치되는 게 있으면 그 Block 반환, 없으면 null. */
  function findBlock(src: string): Block | null {
    if (!src) return null;
    for (const b of modalBlocks) {
      if (b.enabled && b.pattern && src.includes(b.pattern)) return b;
    }
    return null;
  }
  async function toggleExclude(filename: string, currently: boolean) {
    if (!modalProduct) return;
    const code = modalProduct.productCode;
    const url = `${API_BASE}/advertisers/${advertiserId}/products/${code}/images/${filename}/exclude`;
    const res = await fetch(url, currently ? { method: "DELETE" } : { method: "POST" });
    if (res.ok) {
      setModalImages((arr) => arr.map((m) => m.filename === filename ? { ...m, excluded: !currently } : m));
    }
  }

  /** 이 이미지의 정확한 URL을 광고주 차단 목록에 추가 (확인 prompt). */
  async function addBlockFromUrl(src: string) {
    if (!src) return;
    const ok = window.confirm(
      `이 URL을 광고주 #${advertiserId}의 차단 목록에 추가할까요?\n\n${src}\n\n` +
      `→ 다음 [Fetch]부터 이 URL은 다운로드 자체가 안 됩니다.`,
    );
    if (!ok) return;
    try {
      const r = await fetch(`${API_BASE}/advertisers/${advertiserId}/image-blocks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pattern: src, notes: `from ${modalProduct?.productCode}` }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await reloadBlocks();
    } catch (e) {
      alert(`실패: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  async function removeBlock(blockId: number) {
    try {
      const r = await fetch(`${API_BASE}/advertisers/${advertiserId}/image-blocks/${blockId}`, { method: "DELETE" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await reloadBlocks();
    } catch (e) {
      alert(`실패: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  async function load() {
    setError(null);
    try {
      const qs = new URLSearchParams({ page: String(page), size: String(size), filter });
      const res = await fetch(`${API_BASE}/advertisers/${advertiserId}/products?${qs}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setData(await res.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [advertiserId, page, filter]);

  async function reanalyze(code: string) {
    const cost = enrichModel.includes("haiku") ? "~$0.157" : "~$0.032";
    if (!confirm(`이미 enriched된 상품입니다.\n재분석 시 비용(${cost}/상품)이 발생합니다.\n계속하시겠습니까?`)) return;
    setBusy(`${code}-llm`);
    setError(null);
    try {
      const sp = new URLSearchParams({ model: enrichModel, imageLimit: String(enrichImageLimit), force: "true" });
      const res = await fetch(`${API_BASE}/advertisers/${advertiserId}/products/${code}/enrich/llm?${sp}`, { method: "POST" });
      const text = await res.text();
      if (!res.ok) throw new Error(`HTTP ${res.status}: ${text.slice(0, 400)}`);
      setResults((r) => ({ ...r, [`${code}-llm`]: JSON.parse(text) }));
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function action(code: string, kind: "scrape" | "ocr" | "vlm" | "llm") {
    let path: string;
    let qs = "";
    if (kind === "scrape") {
      path = "scrape";
    } else if (kind === "llm") {
      path = "enrich/llm";
      const sp = new URLSearchParams({ model: enrichModel, imageLimit: String(enrichImageLimit) });
      qs = `?${sp}`;
    } else {
      path = `enrich/${kind}`;
    }
    setBusy(`${code}-${kind}`);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/advertisers/${advertiserId}/products/${code}/${path}${qs}`, { method: "POST" });
      const text = await res.text();
      if (!res.ok) throw new Error(`HTTP ${res.status}: ${text.slice(0, 400)}`);
      setResults((r) => ({ ...r, [`${code}-${kind}`]: JSON.parse(text) }));
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  const totalPages = data ? Math.ceil(data.total / size) : 0;

  const filteredCommonImages = !commonImages ? [] : commonImages.filter((i) =>
    commonFilter === "all" ? true :
    commonFilter === "blocked" ? isBlocked(i.src_url) :
    !isBlocked(i.src_url)
  );

  return (
    <main className="mx-auto max-w-6xl p-8 font-sans">
      <header className="mb-4">
        <a href="/sync" className="text-sm text-blue-600">← 광고주 목록</a>
        <h1 className="text-2xl font-bold mt-2">광고주 #{advertiserId} 상품 정제</h1>
        <p className="text-sm text-gray-600 mt-1">
          [1] 스크랩 (HTML+이미지) → [2] OCR 또는 VLM 정제 (둘 중 하나) → enriched_info
        </p>
      </header>

      {/* 스크랩 배치 진행 UI */}
      {batch && batch.status !== "IDLE" && (
        <section className={`mb-3 p-3 rounded border ${batch.status === "RUNNING" ? "border-blue-300 bg-blue-50" : batch.status === "COMPLETED" ? "border-green-300 bg-green-50" : batch.status === "CANCELLED" ? "border-orange-300 bg-orange-50" : "border-red-300 bg-red-50"}`}>
          <div className="flex items-center justify-between mb-2 text-sm">
            <div>
              <span className="font-semibold">📥 스크랩 · {batch.status}</span>
              {batch.status === "RUNNING" && batch.currentCode && (
                <span className="ml-2 text-xs text-gray-600 font-mono">진행 중: {batch.currentCode}</span>
              )}
            </div>
            <div className="text-xs text-gray-600">
              {batch.done.toLocaleString()} / {batch.total.toLocaleString()}
              {" · "}성공 {batch.ok} · 실패 {batch.fail}
              {" · "}경과 {Math.floor(batch.elapsedMs / 60000)}분 {Math.floor((batch.elapsedMs / 1000) % 60)}초
              {batch.status === "RUNNING" && batch.done > 0 && (
                <> · 잔여 ~{Math.round((batch.total - batch.done) * (batch.elapsedMs / batch.done) / 60000)}분</>
              )}
            </div>
          </div>
          <div className="h-2 rounded bg-gray-200 overflow-hidden">
            <div
              className={`h-full transition-all ${batch.status === "RUNNING" ? "bg-blue-500" : batch.status === "COMPLETED" ? "bg-green-500" : batch.status === "CANCELLED" ? "bg-orange-500" : "bg-red-500"}`}
              style={{ width: batch.total > 0 ? `${(batch.done / batch.total) * 100}%` : "0%" }}
            />
          </div>
          {batch.lastError && (
            <p className="mt-2 text-xs text-red-700 font-mono truncate" title={batch.lastError}>최근 에러: {batch.lastError}</p>
          )}
          {batch.status === "RUNNING" && (
            <button onClick={cancelBatch} className="mt-2 px-3 py-1 rounded border border-red-400 text-red-700 text-xs">취소</button>
          )}
        </section>
      )}

      {/* Enrich 배치 진행 UI */}
      {enrichBatch && enrichBatch.status !== "IDLE" && (
        <section className={`mb-4 p-3 rounded border ${enrichBatch.status === "RUNNING" ? "border-purple-300 bg-purple-50" : enrichBatch.status === "COMPLETED" ? "border-green-300 bg-green-50" : enrichBatch.status === "CANCELLED" ? "border-orange-300 bg-orange-50" : "border-red-300 bg-red-50"}`}>
          <div className="flex items-center justify-between mb-2 text-sm">
            <div>
              <span className="font-semibold">🤖 Enrich (LLM) · {enrichBatch.status}</span>
              {enrichBatch.status === "RUNNING" && enrichBatch.currentCode && (
                <span className="ml-2 text-xs text-gray-600 font-mono">rv_id={enrichBatch.currentCode}</span>
              )}
            </div>
            <div className="text-xs text-gray-600">
              {enrichBatch.done.toLocaleString()} / {enrichBatch.total.toLocaleString()}
              {" · "}성공 {enrichBatch.ok} · 실패 {enrichBatch.fail}
              {" · "}경과 {Math.floor(enrichBatch.elapsedMs / 60000)}분 {Math.floor((enrichBatch.elapsedMs / 1000) % 60)}초
              {enrichBatch.status === "RUNNING" && enrichBatch.done > 0 && (
                <> · 잔여 ~{Math.round((enrichBatch.total - enrichBatch.done) * (enrichBatch.elapsedMs / enrichBatch.done) / 60000)}분</>
              )}
            </div>
          </div>
          <div className="h-2 rounded bg-gray-200 overflow-hidden">
            <div
              className={`h-full transition-all ${enrichBatch.status === "RUNNING" ? "bg-purple-500" : enrichBatch.status === "COMPLETED" ? "bg-green-500" : enrichBatch.status === "CANCELLED" ? "bg-orange-500" : "bg-red-500"}`}
              style={{ width: enrichBatch.total > 0 ? `${(enrichBatch.done / enrichBatch.total) * 100}%` : "0%" }}
            />
          </div>
          {enrichBatch.lastError && (
            <p className="mt-2 text-xs text-red-700 font-mono truncate" title={enrichBatch.lastError}>최근 에러: {enrichBatch.lastError}</p>
          )}
          {enrichBatch.status === "RUNNING" && (
            <button onClick={cancelEnrichBatch} className="mt-2 px-3 py-1 rounded border border-red-400 text-red-700 text-xs">취소</button>
          )}
        </section>
      )}

      <div className="flex items-center gap-3 mb-4">
        <div className="flex gap-1">
          {FILTERS.map((f) => (
            <button
              key={f.value}
              onClick={() => { setFilter(f.value); setPage(0); }}
              className={`px-3 py-1.5 rounded text-xs ${filter === f.value ? "bg-black text-white" : "bg-gray-200 text-gray-700"}`}
            >
              {f.label}
            </button>
          ))}
        </div>

        {/* 탭 — scrape 크롤링 / enrich 멀티모달 정제 / 공통 이미지 필터 */}
        <div className="flex gap-1 ml-2">
          <button
            onClick={() => setActiveTab("scrape")}
            className={`px-2.5 py-1 rounded-l text-xs border ${activeTab === "scrape" ? "bg-blue-700 text-white border-blue-700" : "bg-white text-gray-700 border-gray-300"}`}
          >📥 크롤링</button>
          <button
            onClick={() => setActiveTab("enrich")}
            className={`px-2.5 py-1 text-xs border-t border-b ${activeTab === "enrich" ? "bg-purple-700 text-white border-purple-700" : "bg-white text-gray-700 border-gray-300"}`}
          >🤖 Enrich</button>
          <button
            onClick={() => setActiveTab("images")}
            className={`px-2.5 py-1 rounded-r text-xs border ${activeTab === "images" ? "bg-orange-600 text-white border-orange-600" : "bg-white text-gray-700 border-gray-300"}`}
          >🖼️ 공통 이미지{commonImages ? ` (${commonImages.length})` : ""}</button>
        </div>

        {/* 행별 Enrich 단일 호출용 빠른 설정 — 탭과 무관하게 항상 보임 */}
        <div className="flex items-center gap-1 px-2 py-1 rounded border bg-purple-50/40" title="상품 행의 🤖 Enrich 버튼이 이 설정으로 동작">
          <span className="text-[10px] text-purple-700">행별 LLM</span>
          <select
            value={enrichModel}
            onChange={(e) => setEnrichModel(e.target.value as "haiku" | "sonnet" | "opus")}
            className="rounded border-gray-300 px-1 py-0.5 text-xs"
          >
            <option value="haiku">haiku</option>
            <option value="sonnet">sonnet</option>
            <option value="opus">opus</option>
          </select>
          <input
            type="number"
            min={1}
            max={100}
            value={enrichImageLimit}
            onChange={(e) => setEnrichImageLimit(Math.max(1, Math.min(100, Number(e.target.value) || 1)))}
            className="w-14 rounded border-gray-300 px-1 py-0.5 text-xs text-right"
            title="이미지 N장 (1~100)"
          />
          <span className="text-[10px] text-gray-400">장/100</span>
        </div>

        {activeTab === "scrape" && batch?.status !== "RUNNING" && (
          <div className="flex items-center gap-1.5 px-2 py-1 rounded border bg-white">
            <select
              value={batchMode}
              onChange={(e) => setBatchMode(e.target.value as "api" | "page")}
              className="rounded border-gray-300 px-1 py-0.5 text-xs"
              title="api: 메이크샵 OpenAPI product_content (빠름) / page: 광고주 사이트 HTML 다운 (기존)"
            >
              <option value="api">api</option>
              <option value="page">page</option>
            </select>
            <span className="text-xs text-gray-600">개수</span>
            <input
              type="number"
              min={1}
              max={10000}
              value={batchCount}
              onChange={(e) => setBatchCount(Number(e.target.value))}
              className="w-16 rounded border-gray-300 px-1 py-0.5 text-xs text-right"
            />
            {batchMode === "api" && (
              <>
                <span className="text-xs text-gray-600 ml-1">묶음</span>
                <input
                  type="number"
                  min={1}
                  max={200}
                  value={batchSize}
                  onChange={(e) => setBatchSize(Number(e.target.value))}
                  className="w-12 rounded border-gray-300 px-1 py-0.5 text-xs text-right"
                />
              </>
            )}
            <span className="text-xs text-gray-600 ml-1">워커</span>
            <input
              type="number"
              min={1}
              max={32}
              value={batchConcurrency}
              onChange={(e) => setBatchConcurrency(Number(e.target.value))}
              className="w-12 rounded border-gray-300 px-1 py-0.5 text-xs text-right"
            />
            <span className="text-xs text-gray-600 ml-1">sleep</span>
            <input
              type="number"
              min={0}
              max={10000}
              step={100}
              value={batchIntervalMs}
              onChange={(e) => setBatchIntervalMs(Number(e.target.value))}
              className="w-16 rounded border-gray-300 px-1 py-0.5 text-xs text-right"
            />
            <span className="text-xs text-gray-600">ms</span>
            <button onClick={startBatch} className="ml-2 px-2.5 py-1 rounded bg-blue-700 text-white text-xs">시작</button>
          </div>
        )}

        {activeTab === "enrich" && enrichBatch?.status !== "RUNNING" && (
          <div className="flex items-center gap-1.5 px-2 py-1 rounded border bg-white">
            <span className="text-xs text-gray-600">모델</span>
            <select
              value={enrichModel}
              onChange={(e) => setEnrichModel(e.target.value as "haiku" | "sonnet" | "opus")}
              className="rounded border-gray-300 px-1 py-0.5 text-xs"
              title="haiku: 가장 저렴/빠름 · sonnet: 정확도 ↑ · opus: 최고급(느림/비쌈)"
            >
              <option value="haiku">haiku (저렴)</option>
              <option value="sonnet">sonnet (정확)</option>
              <option value="opus">opus (최고급)</option>
            </select>
            <span className="text-xs text-gray-600 ml-1">개수</span>
            <input
              type="number"
              min={1}
              max={10000}
              value={enrichCount}
              onChange={(e) => setEnrichCount(Number(e.target.value))}
              className="w-16 rounded border-gray-300 px-1 py-0.5 text-xs text-right"
            />
            <span className="text-xs text-gray-600 ml-1">이미지</span>
            <input
              type="number"
              min={1}
              max={100}
              value={enrichImageLimit}
              onChange={(e) => setEnrichImageLimit(Math.max(1, Math.min(100, Number(e.target.value) || 1)))}
              className="w-14 rounded border-gray-300 px-1 py-0.5 text-xs text-right"
              title="상품당 이미지 N장 (Claude API 한도 100장, 권장 5~30장)"
            />
            <span className="text-[10px] text-gray-400">/100</span>
            <span className="text-xs text-gray-600 ml-1">워커</span>
            <input
              type="number"
              min={1}
              max={8}
              value={enrichConcurrency}
              onChange={(e) => setEnrichConcurrency(Number(e.target.value))}
              className="w-12 rounded border-gray-300 px-1 py-0.5 text-xs text-right"
            />
            <span className="text-xs text-gray-600 ml-1">sleep</span>
            <input
              type="number"
              min={0}
              max={10000}
              step={500}
              value={enrichIntervalMs}
              onChange={(e) => setEnrichIntervalMs(Number(e.target.value))}
              className="w-16 rounded border-gray-300 px-1 py-0.5 text-xs text-right"
            />
            <span className="text-xs text-gray-600">ms</span>
            <span className="text-xs text-gray-600 ml-1">벡터</span>
            <select
              value={enrichEmbedBackend}
              onChange={(e) => setEnrichEmbedBackend(e.target.value as "bge" | "openai" | "both")}
              className="rounded border-gray-300 px-1 py-0.5 text-xs"
              title="enrich 직후 벡터 임베딩 모델"
            >
              <option value="bge">bge (로컬)</option>
              <option value="openai">openai</option>
              <option value="both">both</option>
            </select>
            <button onClick={startEnrichBatch} className="ml-2 px-2.5 py-1 rounded bg-purple-700 text-white text-xs">시작</button>
          </div>
        )}

        <div className="ml-auto text-sm text-gray-500">
          {data ? (
            <span>
              {data.counts && (
                <span className="mr-3 font-mono text-xs">
                  전체 <span className="text-black">{data.counts.total.toLocaleString()}</span>
                  {" · "}
                  남음 <span className="text-blue-600 font-semibold">{data.counts.pending.toLocaleString()}</span>
                  {" · "}
                  스크랩 <span className="text-green-700">{(data.counts.total - data.counts.pending).toLocaleString()}</span>
                  {" · "}
                  LLM <span className="text-purple-700">{(data.counts.llm_enriched ?? 0).toLocaleString()}</span>
                  {" · "}
                  벡터 <span className="text-emerald-700">{(data.counts.vectorized ?? 0).toLocaleString()}</span>
                </span>
              )}
              <span>{data.total.toLocaleString()}개 · page {page + 1} / {totalPages || 1}</span>
            </span>
          ) : "loading..."}
        </div>
        <div className="flex gap-1">
          <button onClick={() => setPage(Math.max(0, page - 1))} disabled={page === 0} className="px-2 py-1 rounded border text-xs disabled:opacity-30">‹</button>
          <button onClick={() => setPage(page + 1)} disabled={!data || page >= totalPages - 1} className="px-2 py-1 rounded border text-xs disabled:opacity-30">›</button>
        </div>
      </div>

      {error && (
        <div className="p-3 mb-4 rounded border border-red-300 bg-red-50 text-red-800 text-sm whitespace-pre-wrap">
          {error}
        </div>
      )}

      {modalProduct && (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4" onClick={closeModal}>
          <div className="bg-white rounded-lg max-w-6xl w-full max-h-[90vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
            <header className="p-4 border-b flex items-center justify-between">
              <div className="min-w-0 flex-1">
                <h3 className="font-semibold truncate">{modalProduct.productName}</h3>
                <p className="text-xs text-gray-500 font-mono">
                  #{modalProduct.productCode} · {modalImages.length}장 ·
                  제외 {modalImages.filter(m => m.excluded).length}장 ·
                  URL차단 {modalImages.filter(m => findBlock(m.src)).length}장 ·
                  광고주 차단룰 {modalBlocks.length}개
                </p>
              </div>
              <button onClick={closeModal} className="px-3 py-1 rounded border text-sm">닫기</button>
            </header>
            <div className="p-4 overflow-y-auto flex-1">
              {modalLoading && <p className="text-sm text-gray-500">loading...</p>}
              <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6 gap-3">
                {modalImages.map((m) => {
                  const block = findBlock(m.src);
                  const isBlocked = !!block;
                  const cardClass = isBlocked
                    ? "relative group border-2 border-amber-500 rounded overflow-hidden opacity-50 ring-2 ring-amber-300"
                    : m.excluded
                      ? "relative group border-2 border-red-400 rounded overflow-hidden opacity-40"
                      : "relative group border rounded overflow-hidden";
                  return (
                    <div key={m.filename} className={cardClass}>
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={`${API_BASE}/advertisers/${advertiserId}/products/${modalProduct.productCode}/images/${m.filename}`}
                        alt={m.filename}
                        className="w-full h-32 object-cover bg-gray-100"
                      />
                      {isBlocked && (
                        <div className="absolute top-1 left-1 px-1.5 py-0.5 rounded bg-amber-500 text-white text-[10px] font-bold shadow">
                          URL 차단됨
                        </div>
                      )}
                      {m.excluded && !isBlocked && (
                        <div className="absolute top-1 left-1 px-1.5 py-0.5 rounded bg-red-600 text-white text-[10px] font-bold shadow">
                          이미지 제외
                        </div>
                      )}
                      <div className="absolute inset-0 bg-black/0 group-hover:bg-black/40 transition flex flex-col items-stretch justify-end gap-1 p-1">
                        <button
                          onClick={() => toggleExclude(m.filename, m.excluded)}
                          className={`text-xs px-2 py-1 rounded ${m.excluded ? "bg-green-600 text-white" : "bg-red-600 text-white"} opacity-0 group-hover:opacity-100 transition`}
                        >
                          {m.excluded ? "이 이미지 복원" : "이 이미지 제외"}
                        </button>
                        {m.src && (
                          isBlocked ? (
                            <button
                              onClick={() => removeBlock(block!.id)}
                              className="text-xs px-2 py-1 rounded bg-green-700 text-white opacity-0 group-hover:opacity-100 transition"
                              title={`매치된 패턴: ${block!.pattern}`}
                            >
                              URL 차단 해제
                            </button>
                          ) : (
                            <button
                              onClick={() => addBlockFromUrl(m.src)}
                              className="text-xs px-2 py-1 rounded bg-amber-500 text-white opacity-0 group-hover:opacity-100 transition"
                              title={m.src}
                            >
                              이 URL 차단
                            </button>
                          )
                        )}
                      </div>
                      <div className="px-1 py-0.5">
                        <p className="text-[10px] text-gray-500 font-mono">{(m.size / 1024).toFixed(0)}KB</p>
                        {m.src && (
                          <p className="text-[10px] text-blue-600 font-mono truncate" title={m.src}>
                            {m.src.replace(/^https?:\/\//, "").slice(0, 50)}
                          </p>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
            <footer className="p-3 border-t text-xs text-gray-500 space-y-1">
              <p><span className="text-red-600">이 이미지 제외</span> — 이 상품에서만 skip (디스크 파일 보존, 복원 가능)</p>
              <p><span className="text-amber-600">이 URL 차단</span> — 광고주의 모든 상품에서 이 URL은 다음 fetch부터 다운로드 안 함 (정확히 일치)</p>
              <p className="text-gray-400">광고주 [수정] 페이지에서 짧게 줄이면 substring 패턴으로 광범위 차단 가능 (예: "/event/")</p>
            </footer>
          </div>
        </div>
      )}

      {/* ── 공통 이미지 탭 ─────────────────────────────────────── */}
      {activeTab === "images" && (
        <div className="mt-4">
          {/* 안내 배너 */}
          <div className="mb-3 p-3 rounded border border-orange-200 bg-orange-50 text-sm">
            <p className="font-semibold text-orange-800">⚠️ 공통 이미지 감지 — enrich 전 확인 권장</p>
            <p className="text-orange-700 mt-0.5 text-xs">
              여러 상품 페이지에 동일 URL로 반복 등장하는 이미지입니다. 배너·공지·이벤트 이미지일 수 있습니다.
              LLM enrich 시 <strong>자동으로 제외</strong>됩니다 (기준: {commonMinProducts}개 이상 상품).
            </p>
          </div>

          {/* 툴바 */}
          <div className="flex items-center gap-2 mb-3">
            <label className="text-xs text-gray-600">최소 상품 수</label>
            <input
              type="number" min={2} max={100}
              value={commonMinProducts}
              onChange={(e) => setCommonMinProducts(Number(e.target.value))}
              className="w-16 rounded border border-gray-300 px-1.5 py-0.5 text-xs"
            />
            <button
              onClick={() => { setCommonImages(null); setSelectedCommonImage(null); loadCommonImages(commonMinProducts); }}
              className="px-3 py-1 rounded bg-orange-600 text-white text-xs"
            >새로고침</button>
            {commonImages && <span className="text-xs text-gray-500">총 {commonImages.length}개 URL</span>}

            {/* 차단 필터 탭 */}
            <div className="flex gap-0.5 border rounded overflow-hidden">
              {([["all", "전체"], ["unblocked", "미차단"], ["blocked", "차단됨"]] as const).map(([val, label]) => (
                <button
                  key={val}
                  onClick={() => setCommonFilter(val)}
                  className={`px-2.5 py-1 text-xs transition-colors ${commonFilter === val ? (val === "blocked" ? "bg-red-600 text-white" : "bg-gray-800 text-white") : "bg-white text-gray-600 hover:bg-gray-100"}`}
                >{label}{commonImages && val !== "all" && (
                  <span className="ml-1 opacity-70">
                    ({commonImages.filter(i => val === "blocked" ? isBlocked(i.src_url) : !isBlocked(i.src_url)).length})
                  </span>
                )}</button>
              ))}
            </div>

            {/* 뷰 모드 토글 */}
            <div className="flex gap-0.5 ml-auto border rounded overflow-hidden">
              <button
                onClick={() => setCommonViewMode("list")}
                title="목록 + 미리보기"
                className={`px-2.5 py-1 text-xs transition-colors ${commonViewMode === "list" ? "bg-gray-800 text-white" : "bg-white text-gray-600 hover:bg-gray-100"}`}
              >☰ 목록</button>
              <button
                onClick={() => setCommonViewMode("grid")}
                title="사진첩"
                className={`px-2.5 py-1 text-xs transition-colors ${commonViewMode === "grid" ? "bg-gray-800 text-white" : "bg-white text-gray-600 hover:bg-gray-100"}`}
              >⊞ 사진첩</button>
            </div>
          </div>

          {commonImagesLoading && <p className="text-gray-500 text-sm py-8 text-center">로딩 중...</p>}

          {commonImages && !commonImagesLoading && filteredCommonImages.length === 0 && (
            <p className="p-8 text-gray-400 text-sm text-center">
              {commonImages.length === 0
                ? `공통 이미지 없음 (기준 ${commonMinProducts}개 이상)`
                : commonFilter === "blocked"
                ? "차단된 이미지 없음"
                : "미차단 이미지 없음"}
            </p>
          )}

          {/* ── 목록 + 미리보기 뷰 ── */}
          {commonImages && !commonImagesLoading && filteredCommonImages.length > 0 && commonViewMode === "list" && (
            <>
              {/* 선택 일괄 차단 툴바 */}
              {checkedSrcs.size > 0 && (
                <div className="mb-2 flex items-center gap-2 px-3 py-2 rounded bg-orange-50 border border-orange-200">
                  <span className="text-xs text-orange-700 font-semibold">{checkedSrcs.size}개 선택됨</span>
                  <button
                    onClick={() => bulkAddImageBlocks([...checkedSrcs])}
                    className="px-3 py-1 rounded bg-red-600 text-white text-xs"
                  >선택 항목 차단 등록</button>
                  <button
                    onClick={() => setCheckedSrcs(new Set())}
                    className="px-3 py-1 rounded border text-xs text-gray-600"
                  >선택 해제</button>
                </div>
              )}
              <div className="flex gap-4" style={{ minHeight: 480 }}>
                {/* 좌: 목록 */}
                <div className="w-1/2 border rounded overflow-y-auto" style={{ maxHeight: 600 }}>
                  <table className="w-full text-xs">
                    <thead className="sticky top-0 bg-gray-50 border-b z-10">
                      <tr>
                        <th className="px-2 py-1.5 w-8">
                          <input
                            type="checkbox"
                            checked={filteredCommonImages.length > 0 && filteredCommonImages.every((i) => checkedSrcs.has(i.src_url))}
                            onChange={(e) => {
                              const next = new Set(checkedSrcs);
                              filteredCommonImages.forEach((i) => e.target.checked ? next.add(i.src_url) : next.delete(i.src_url));
                              setCheckedSrcs(next);
                            }}
                          />
                        </th>
                        <th className="px-2 py-1.5 text-left font-medium text-gray-600 w-14">상품수</th>
                        <th className="px-2 py-1.5 text-left font-medium text-gray-600">이미지 URL</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredCommonImages.map((img) => (
                        <tr
                          key={img.src_url}
                          onClick={() => setSelectedCommonImage(img)}
                          className={`cursor-pointer border-b transition-colors ${
                            isBlocked(img.src_url)
                              ? "bg-red-50 hover:bg-red-100"
                              : selectedCommonImage?.src_url === img.src_url
                              ? "bg-orange-100"
                              : "hover:bg-orange-50"
                          }`}
                        >
                          <td className="px-2 py-1.5" onClick={(e) => e.stopPropagation()}>
                            <input
                              type="checkbox"
                              checked={checkedSrcs.has(img.src_url)}
                              onChange={(e) => {
                                const next = new Set(checkedSrcs);
                                e.target.checked ? next.add(img.src_url) : next.delete(img.src_url);
                                setCheckedSrcs(next);
                              }}
                            />
                          </td>
                          <td className="px-2 py-1.5 font-mono text-orange-700 font-semibold">{img.product_count}</td>
                          <td className="px-2 py-1.5 text-gray-700 break-all leading-tight">
                            <div className="flex items-center gap-1.5 flex-wrap">
                              {isBlocked(img.src_url) && (
                                <span className="shrink-0 px-1.5 py-0.5 rounded bg-red-100 text-red-600 text-[10px] font-semibold border border-red-200">차단됨</span>
                              )}
                              <span className="text-[10px] font-mono">{img.src_url}</span>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {/* 우: 미리보기 */}
                <div className="w-1/2 border rounded flex flex-col items-center justify-center bg-gray-50 p-4">
                  {!selectedCommonImage ? (
                    <p className="text-gray-400 text-sm">← 좌측 목록에서 항목을 클릭하면 이미지를 확인할 수 있습니다</p>
                  ) : (
                    <>
                      <span className="mb-2 px-2 py-0.5 rounded bg-orange-100 text-orange-700 text-xs font-semibold">
                        {selectedCommonImage.product_count}개 상품에 공통 등장
                      </span>
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        key={selectedCommonImage.src_url}
                        src={selectedCommonImage.src_url}
                        alt="공통 이미지"
                        className="max-w-full max-h-52 object-contain rounded border bg-white"
                        onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
                      />
                      <p className="mt-2 text-[10px] text-gray-500 font-mono break-all text-center leading-relaxed px-2">
                        {selectedCommonImage.src_url}
                      </p>
                      {isBlocked(selectedCommonImage.src_url) ? (
                        <button
                          onClick={async () => {
                            const entry = getBlockEntry(selectedCommonImage.src_url);
                            if (!entry) return;
                            if (!confirm(`차단을 해제할까요?\n${selectedCommonImage.src_url}`)) return;
                            await removeImageBlock(entry.id);
                          }}
                          className="mt-3 px-3 py-1.5 rounded bg-gray-600 text-white text-xs"
                        >🔓 차단 해제</button>
                      ) : (
                        <button
                          onClick={async () => {
                            if (!confirm(`이 URL을 차단 패턴에 추가할까요?\n${selectedCommonImage.src_url}`)) return;
                            await addImageBlock(selectedCommonImage.src_url);
                            await loadBlockPatterns();
                            alert("차단 패턴 등록 완료");
                          }}
                          className="mt-3 px-3 py-1.5 rounded bg-red-600 text-white text-xs"
                        >🚫 차단 등록</button>
                      )}
                    </>
                  )}
                </div>
              </div>
            </>
          )}

          {/* ── 사진첩(그리드) 뷰 ── */}
          {commonImages && !commonImagesLoading && filteredCommonImages.length > 0 && commonViewMode === "grid" && (
            <>
              {/* 팝업 모달 */}
              {selectedCommonImage && (
                <div
                  className="fixed inset-0 bg-black/70 z-50 flex items-center justify-center p-6"
                  onClick={() => { setSelectedCommonImage(null); setPopupProducts(null); }}
                >
                  <div
                    className="bg-white rounded-xl shadow-2xl max-w-xl w-full p-5 flex flex-col"
                    onClick={(e) => e.stopPropagation()}
                  >
                    {/* 헤더 */}
                    <div className="flex items-center justify-between mb-3">
                      <span className="px-2 py-0.5 rounded bg-orange-100 text-orange-700 text-xs font-semibold">
                        {selectedCommonImage.product_count}개 상품에 공통 등장
                      </span>
                      <button
                        onClick={() => { setSelectedCommonImage(null); setPopupProducts(null); }}
                        className="text-gray-400 hover:text-gray-700 text-xl leading-none"
                      >×</button>
                    </div>
                    {/* 이미지 */}
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      key={selectedCommonImage.src_url}
                      src={selectedCommonImage.src_url}
                      alt="공통 이미지"
                      className="max-w-full max-h-72 object-contain rounded border bg-gray-50 self-center"
                      onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
                    />
                    {/* URL */}
                    <p className="mt-2 text-[10px] text-gray-500 font-mono break-all leading-relaxed">
                      {selectedCommonImage.src_url}
                    </p>
                    {/* 사용 상품 목록 */}
                    <div className="mt-3 border rounded overflow-hidden">
                      <p className="px-2 py-1.5 bg-gray-50 border-b text-xs font-medium text-gray-600">
                        사용 상품 (최대 5개)
                      </p>
                      {popupProducts === null ? (
                        <p className="px-2 py-2 text-xs text-gray-400">로딩 중...</p>
                      ) : popupProducts.length === 0 ? (
                        <p className="px-2 py-2 text-xs text-gray-400">상품 정보 없음</p>
                      ) : (
                        <ul className="divide-y">
                          {popupProducts.map((p) => (
                            <li key={p.product_code} className="px-2 py-1.5 flex items-center gap-2 text-xs">
                              <span className="text-gray-400 font-mono shrink-0">#{p.product_code}</span>
                              <span className="truncate text-gray-700 flex-1" title={p.product_name}>{p.product_name || "(이름 없음)"}</span>
                              {p.product_url && (
                                <a
                                  href={p.product_url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="shrink-0 px-1.5 py-0.5 rounded border text-blue-600 hover:bg-blue-50"
                                  onClick={(e) => e.stopPropagation()}
                                >이동 →</a>
                              )}
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                    {/* 차단 버튼 */}
                    {isBlocked(selectedCommonImage.src_url) ? (
                      <button
                        onClick={async () => {
                          const entry = getBlockEntry(selectedCommonImage.src_url);
                          if (!entry) return;
                          if (!confirm(`차단을 해제할까요?\n${selectedCommonImage.src_url}`)) return;
                          await removeImageBlock(entry.id);
                          setSelectedCommonImage(null);
                          setPopupProducts(null);
                        }}
                        className="mt-3 px-4 py-2 rounded bg-gray-600 text-white text-xs font-semibold"
                      >🔓 차단 해제</button>
                    ) : (
                      <button
                        onClick={async () => {
                          if (!confirm(`이 URL을 차단 패턴에 추가할까요?\n${selectedCommonImage.src_url}`)) return;
                          await addImageBlock(selectedCommonImage.src_url);
                          await loadBlockPatterns();
                          setSelectedCommonImage(null);
                          setPopupProducts(null);
                        }}
                        className="mt-3 px-4 py-2 rounded bg-red-600 text-white text-xs font-semibold"
                      >🚫 차단 등록</button>
                    )}
                    <p className="mt-1 text-[10px] text-gray-400 text-center">배경 클릭으로 닫기</p>
                  </div>
                </div>
              )}
              {/* 그리드 전체선택 툴바 */}
              <div className="mb-2 flex items-center gap-2">
                <label className="flex items-center gap-1.5 cursor-pointer text-xs text-gray-600 select-none">
                  <input
                    type="checkbox"
                    checked={filteredCommonImages.length > 0 && filteredCommonImages.every((i) => checkedSrcs.has(i.src_url))}
                    onChange={(e) => {
                      const next = new Set(checkedSrcs);
                      filteredCommonImages.forEach((i) => e.target.checked ? next.add(i.src_url) : next.delete(i.src_url));
                      setCheckedSrcs(next);
                    }}
                  />
                  전체 선택
                </label>
                {checkedSrcs.size > 0 && (
                  <>
                    <span className="text-xs text-orange-700 font-semibold">{checkedSrcs.size}개 선택됨</span>
                    <button
                      onClick={() => bulkAddImageBlocks([...checkedSrcs])}
                      className="px-3 py-1 rounded bg-red-600 text-white text-xs"
                    >선택 항목 차단 등록</button>
                    <button
                      onClick={() => setCheckedSrcs(new Set())}
                      className="px-3 py-1 rounded border text-xs text-gray-600"
                    >선택 해제</button>
                  </>
                )}
              </div>

              <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))" }}>
                {filteredCommonImages.map((img) => (
                  <div
                    key={img.src_url}
                    className={`relative rounded border overflow-hidden bg-white transition-colors ${
                      checkedSrcs.has(img.src_url)
                        ? "border-orange-500 ring-2 ring-orange-300"
                        : isBlocked(img.src_url)
                        ? "border-red-400 ring-1 ring-red-200"
                        : "border-gray-200 hover:border-orange-400"
                    }`}
                  >
                    {/* 체크박스 */}
                    <div
                      className="absolute top-1 left-1 z-10"
                      onClick={(e) => {
                        e.stopPropagation();
                        const next = new Set(checkedSrcs);
                        checkedSrcs.has(img.src_url) ? next.delete(img.src_url) : next.add(img.src_url);
                        setCheckedSrcs(next);
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={checkedSrcs.has(img.src_url)}
                        onChange={() => {}}
                        className="cursor-pointer"
                      />
                    </div>
                    {/* 이미지 클릭 → 팝업 */}
                    <div className="cursor-pointer" onClick={() => openGridPopup(img)}>
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={img.src_url}
                        alt=""
                        className="w-full h-24 object-cover"
                        onError={(e) => {
                          const el = e.target as HTMLImageElement;
                          el.style.display = "none";
                          const fallback = el.nextSibling as HTMLElement | null;
                          if (fallback) fallback.style.display = "flex";
                        }}
                      />
                      <div className="hidden h-24 items-center justify-center bg-gray-100 text-gray-400 text-[10px]">이미지 없음</div>
                      <div className="px-1.5 py-1 border-t bg-gray-50">
                        <div className="flex items-center gap-1">
                          <span className="text-[10px] font-mono text-orange-700 font-semibold">{img.product_count}개 상품</span>
                          {isBlocked(img.src_url) && (
                            <span className="px-1 py-0.5 rounded bg-red-100 text-red-600 text-[9px] font-semibold border border-red-200">차단됨</span>
                          )}
                        </div>
                        <p className="text-[9px] text-gray-400 truncate" title={img.src_url}>
                          {img.src_url.split("/").pop()}
                        </p>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      )}

      <ul className="space-y-3" style={{ display: activeTab === "images" ? "none" : undefined }}>
        {(data?.items ?? []).map((p) => {
          const sb = busy?.startsWith(p.productCode + "-");
          return (
            <li key={p.id} className="p-3 border rounded text-sm">
              <div className="flex gap-3">
                {p.imageUrl ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={p.imageUrl} alt="" className="w-20 h-20 object-cover rounded bg-gray-100 shrink-0" />
                ) : <div className="w-20 h-20 bg-gray-100 rounded shrink-0" />}
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-medium truncate">{p.productName}</span>
                    <span className="text-xs font-mono text-gray-500">#{p.productCode}</span>
                  </div>
                  <a href={p.productUrl} target="_blank" rel="noreferrer" className="text-xs text-blue-600 truncate block">
                    {p.productUrl}
                  </a>
                  <div className="flex flex-wrap gap-2 mt-1 text-xs">
                    {p.scrapeStatus && (
                      <span className={`px-1.5 py-0.5 rounded font-mono ${p.scrapeStatus === "scraped" ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800"}`}>
                        scrape: {p.scrapeStatus} ({p.imageLocalCount ?? 0}장, body {p.bodyTextLen ?? 0}자)
                      </span>
                    )}
                    {p.enrichMethod && (
                      <span className="px-1.5 py-0.5 rounded bg-blue-100 text-blue-800 font-mono">
                        enrich: {p.enrichMethod} ({(p.enrichedInfo ?? "").length}자)
                      </span>
                    )}
                    {data?.enrichedIds?.includes(p.id) && (
                      <span className="px-1.5 py-0.5 rounded bg-purple-100 text-purple-800 font-mono">🤖 LLM enriched</span>
                    )}
                    {data?.embeddedIds?.includes(p.id) && (
                      <span className="px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-800 font-mono">📦 벡터 완료</span>
                    )}
                  </div>
                  {p.enrichedInfo && (
                    <details className="mt-2 text-xs">
                      <summary className="cursor-pointer text-gray-600">enriched_info 미리보기</summary>
                      <pre className="mt-1 p-2 bg-gray-50 rounded whitespace-pre-wrap break-words max-h-40 overflow-y-auto">
                        {p.enrichedInfo}
                      </pre>
                    </details>
                  )}
                  {p.bodyText && !p.enrichedInfo && (
                    <details className="mt-2 text-xs">
                      <summary className="cursor-pointer text-gray-600">body_text 미리보기</summary>
                      <pre className="mt-1 p-2 bg-gray-50 rounded whitespace-pre-wrap break-words max-h-40 overflow-y-auto">
                        {p.bodyText}
                      </pre>
                    </details>
                  )}
                </div>
                <div className="flex flex-col gap-1.5 shrink-0">
                  <button
                    onClick={() => action(p.productCode, "scrape")}
                    disabled={!!busy}
                    className="px-2.5 py-1 rounded border text-xs disabled:opacity-30"
                  >
                    {sb && busy?.endsWith("-scrape") ? "..." : "1) Fetch"}
                  </button>
                  <button
                    onClick={() => openImages(p)}
                    disabled={p.scrapeStatus !== "scraped"}
                    className="px-2.5 py-1 rounded border border-gray-400 text-gray-700 text-xs disabled:opacity-30"
                  >
                    이미지 ({p.imageLocalCount ?? 0})
                  </button>
                  <button
                    onClick={() => action(p.productCode, "ocr")}
                    disabled={!!busy || p.scrapeStatus !== "scraped"}
                    className="px-2.5 py-1 rounded border border-amber-400 text-amber-700 text-xs disabled:opacity-30"
                  >
                    {sb && busy?.endsWith("-ocr") ? "..." : "2-A) OCR"}
                  </button>
                  <button
                    onClick={() => action(p.productCode, "vlm")}
                    disabled={!!busy || p.scrapeStatus !== "scraped"}
                    className="px-2.5 py-1 rounded border border-indigo-400 text-indigo-700 text-xs disabled:opacity-30"
                  >
                    {sb && busy?.endsWith("-vlm") ? "..." : "2-B) VLM"}
                  </button>
                  <button
                    onClick={() => action(p.productCode, "llm")}
                    disabled={!!busy || p.scrapeStatus !== "scraped"}
                    className="px-2.5 py-1 rounded bg-purple-700 text-white text-xs disabled:opacity-30"
                    title={`멀티모달 LLM enrich (model=${enrichModel}, 이미지 ${enrichImageLimit}장)`}
                  >
                    {sb && busy?.endsWith("-llm") ? "..." : `🤖 Enrich (${enrichModel}, ${enrichImageLimit}장)`}
                  </button>
                  {data?.enrichedIds?.includes(p.id) && (
                    <>
                      <button
                        onClick={() => openEnrichedDetail(p.id)}
                        className="px-2.5 py-1 rounded bg-gray-800 text-white text-xs"
                        title="enrich/벡터 결과 상세"
                      >
                        🔍 상세
                      </button>
                      <button
                        onClick={() => reanalyze(p.productCode)}
                        disabled={!!busy}
                        className="px-2.5 py-1 rounded bg-orange-600 text-white text-xs disabled:opacity-30"
                        title="기존 enrich 결과를 덮어쓰고 재분석"
                      >
                        {busy === `${p.productCode}-llm` ? "..." : "🔄 재분석"}
                      </button>
                    </>
                  )}
                </div>
              </div>
            </li>
          );
        })}
      </ul>

      {/* Enriched 상세 모달 (search-rv와 동일 디자인) */}
      {(enrichedDetail || enrichedDetailLoading) && (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4" onClick={() => setEnrichedDetail(null)}>
          <div className="bg-white rounded-lg max-w-5xl w-full max-h-[92vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
            <header className="p-4 border-b flex items-center justify-between">
              <div className="min-w-0 flex-1">
                <h3 className="font-semibold truncate">{enrichedDetail?.product_name ?? "로딩 중..."}</h3>
                {enrichedDetail && (
                  <p className="text-xs text-gray-500 font-mono">
                    rv_id={enrichedDetail.rv_product_id} · #{enrichedDetail.product_code} · adv={enrichedDetail.advertiser_id} ·
                    enriched={enrichedDetail.enriched_at?.slice(0, 16) ?? "-"} · cost ${enrichedDetail.cost_usd?.toFixed(4) ?? "?"} · {enrichedDetail.duration_ms ?? "?"}ms
                  </p>
                )}
              </div>
              <button onClick={() => setEnrichedDetail(null)} className="px-3 py-1 rounded border text-sm">닫기</button>
            </header>
            <div className="p-4 overflow-y-auto flex-1 space-y-4 text-sm">
              {enrichedDetailLoading && <p className="text-gray-500">로딩 중...</p>}
              {enrichedDetail && (
                <>
                  <section>
                    <h4 className="font-semibold mb-1">카테고리 / 공통 속성</h4>
                    <div className="flex flex-wrap gap-1 text-xs">
                      {enrichedDetail.category && <span className="px-2 py-0.5 rounded bg-gray-100">{enrichedDetail.category} (conf {enrichedDetail.category_confidence?.toFixed(2)})</span>}
                      {enrichedDetail.brand && <span className="px-2 py-0.5 rounded bg-purple-50 text-purple-700">brand: {enrichedDetail.brand}</span>}
                      {enrichedDetail.brand_tier && <span className="px-2 py-0.5 rounded bg-amber-50 text-amber-700">tier: {enrichedDetail.brand_tier}</span>}
                      {enrichedDetail.target_gender && <span className="px-2 py-0.5 rounded bg-pink-50 text-pink-700">{enrichedDetail.target_gender}</span>}
                      {(enrichedDetail.target_age_min && enrichedDetail.target_age_max) && (
                        <span className="px-2 py-0.5 rounded bg-blue-50 text-blue-700">{enrichedDetail.target_age_min}~{enrichedDetail.target_age_max}대</span>
                      )}
                      {enrichedDetail.origin_country && <span className="px-2 py-0.5 rounded bg-gray-100">{enrichedDetail.origin_country}</span>}
                      {enrichedDetail.manufacturer && <span className="px-2 py-0.5 rounded bg-gray-100">{enrichedDetail.manufacturer}</span>}
                    </div>
                  </section>

                  {enrichedDetail.category_attributes && Object.keys(enrichedDetail.category_attributes).length > 0 && (
                    <section>
                      <h4 className="font-semibold mb-1">category_attributes (JSONB)</h4>
                      <pre className="text-xs bg-gray-50 p-2 rounded overflow-x-auto">{JSON.stringify(enrichedDetail.category_attributes, null, 2)}</pre>
                    </section>
                  )}

                  {enrichedDetail.set_components && enrichedDetail.set_components.length > 0 && (
                    <section>
                      <h4 className="font-semibold mb-1">SET 구성</h4>
                      <pre className="text-xs bg-gray-50 p-2 rounded overflow-x-auto">{JSON.stringify(enrichedDetail.set_components, null, 2)}</pre>
                    </section>
                  )}
                  {enrichedDetail.compatible_products && enrichedDetail.compatible_products.length > 0 && (
                    <section>
                      <h4 className="font-semibold mb-1">호환/관련 상품 (오염 격리)</h4>
                      <pre className="text-xs bg-amber-50 p-2 rounded overflow-x-auto">{JSON.stringify(enrichedDetail.compatible_products, null, 2)}</pre>
                    </section>
                  )}

                  <section>
                    <h4 className="font-semibold mb-1">다관점 설명문</h4>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                      {[
                        { p: "situation", t: enrichedDetail.desc_situation },
                        { p: "material", t: enrichedDetail.desc_material },
                        { p: "style", t: enrichedDetail.desc_style },
                        { p: "persona", t: enrichedDetail.desc_persona },
                      ].map(({ p, t }) => {
                        const m = enrichedDetail.descriptions_meta?.find((x) => x.perspective === p);
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

                  {enrichedDetail.tags && enrichedDetail.tags.length > 0 && (
                    <section>
                      <h4 className="font-semibold mb-1">태그 ({enrichedDetail.tags.length}개)</h4>
                      <div className="flex flex-wrap gap-1 text-[11px]">
                        {enrichedDetail.tags.map((t, i) => (
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

                  {enrichedDetail.image_types && enrichedDetail.image_types.length > 0 && (
                    <section>
                      <h4 className="font-semibold mb-1">이미지 분류 ({enrichedDetail.image_types.length}장)</h4>
                      <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 gap-2">
                        {enrichedDetail.image_types.map((im) => {
                          const used = im.used_for_attributes && im.is_main_product;
                          return (
                            <div key={im.sha1} className={`border rounded overflow-hidden text-[10px] ${used ? "border-green-300" : "border-amber-300 opacity-70"}`}>
                              {/* eslint-disable-next-line @next/next/no-img-element */}
                              <img src={imgSrcFor(enrichedDetail.advertiser_id, enrichedDetail.product_code, im.sha1)} alt="" className="w-full h-20 object-cover bg-gray-100" />
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

                  <section className="text-xs text-gray-500 border-t pt-2">
                    <div className="flex flex-wrap gap-3 font-mono">
                      <span>model: {enrichedDetail.model_used || "-"}</span>
                      <span>in: {enrichedDetail.input_tokens || 0}</span>
                      <span>out: {enrichedDetail.output_tokens || 0}</span>
                      <span>cost: ${enrichedDetail.cost_usd?.toFixed(4) || "?"}</span>
                      <span>time: {enrichedDetail.duration_ms ? `${(enrichedDetail.duration_ms / 1000).toFixed(1)}s` : "?"}</span>
                      <a href={enrichedDetail.product_url} target="_blank" rel="noreferrer" className="text-blue-600 underline">원본 페이지</a>
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
