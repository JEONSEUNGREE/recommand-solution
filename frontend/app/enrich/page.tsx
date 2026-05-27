"use client";

import { useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://192.168.101.27:8090";

type ImageResult = {
  src: string;
  alt: string;
  local_path: string | null;
  ocr_text: string | null;
  error: string | null;
};

type DomValidation = {
  used_selector: string;
  method: "whitelist" | "heuristic" | "fallback";
  container_label: string;
  warnings: string[];
  stripped_nodes: number;
};

type ScrapeResult = {
  url: string;
  title: string;
  meta: Record<string, string>;
  platform: string;
  platform_scores: Record<string, number>;
  dom_validation: DomValidation;
  body_text: string;
  body_text_len: number;
  image_count: number;
  images: ImageResult[];
};

export default function EnrichPage() {
  const [url, setUrl] = useState(
    "https://andar.co.kr/product/detail.html?product_no=17828&cate_no=3954&display_group=1",
  );
  const [maxImages, setMaxImages] = useState(5);
  const [doOcr, setDoOcr] = useState(true);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ScrapeResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState<number | null>(null);

  async function submit() {
    setLoading(true);
    setError(null);
    setResult(null);
    setElapsed(null);
    const t0 = performance.now();
    try {
      const res = await fetch(`${API_BASE}/enrich`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, maxImages, ocr: doOcr }),
      });
      const text = await res.text();
      if (!res.ok) throw new Error(`HTTP ${res.status}: ${text.slice(0, 300)}`);
      setResult(JSON.parse(text) as ScrapeResult);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setElapsed(Math.round(performance.now() - t0));
      setLoading(false);
    }
  }

  return (
    <main className="mx-auto max-w-5xl p-8 font-sans">
      <header className="mb-6">
        <h1 className="text-2xl font-bold">상품 페이지 텍스트 추출 + 이미지 OCR</h1>
        <p className="text-sm text-gray-600 mt-1">
          상품 상세 URL을 넣으면 본문 텍스트와 이미지를 다운로드하고, EasyOCR(한+영, CPU)로 텍스트를 뽑습니다.
        </p>
      </header>

      <section className="space-y-3 mb-6 p-4 border rounded-lg bg-gray-50">
        <label className="block">
          <span className="text-sm font-medium">상품 상세 URL</span>
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            disabled={loading}
            className="mt-1 block w-full rounded border-gray-300 px-3 py-2 font-mono text-sm"
            placeholder="https://..."
          />
        </label>
        <div className="flex items-center gap-4">
          <label className="flex items-center gap-2 text-sm">
            <span>이미지 최대</span>
            <input
              type="number"
              min={0}
              max={50}
              value={maxImages}
              onChange={(e) => setMaxImages(Number(e.target.value))}
              disabled={loading}
              className="w-16 rounded border-gray-300 px-2 py-1"
            />
            <span>장</span>
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={doOcr}
              onChange={(e) => setDoOcr(e.target.checked)}
              disabled={loading}
            />
            <span>OCR 수행</span>
          </label>
          <button
            onClick={submit}
            disabled={loading || !url}
            className="ml-auto px-4 py-2 rounded bg-black text-white text-sm disabled:opacity-50"
          >
            {loading ? "처리 중..." : "추출하기"}
          </button>
        </div>
      </section>

      {elapsed !== null && (
        <p className="text-xs text-gray-500 mb-4">elapsed: {(elapsed / 1000).toFixed(1)}s</p>
      )}
      {error && (
        <div className="p-4 mb-4 rounded border border-red-300 bg-red-50 text-red-800 text-sm whitespace-pre-wrap">
          {error}
        </div>
      )}

      {result && (
        <article className="space-y-6">
          {(() => {
            const dv = result.dom_validation;
            const ok = dv.method === "whitelist" && dv.warnings.length === 0 && result.platform !== "unknown";
            const tone = ok
              ? "border-green-400 bg-green-50 text-green-900"
              : dv.method === "heuristic"
                ? "border-yellow-400 bg-yellow-50 text-yellow-900"
                : "border-orange-400 bg-orange-50 text-orange-900";
            return (
              <section className={`p-4 border-l-4 rounded ${tone}`}>
                <header className="flex items-center justify-between mb-2">
                  <h2 className="font-semibold">DOM 진단</h2>
                  <span className="text-xs font-mono px-2 py-0.5 rounded bg-white/60">
                    {ok ? "OK" : dv.method === "heuristic" ? "HEURISTIC FALLBACK" : "FALLBACK"}
                  </span>
                </header>
                <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
                  <dt className="font-medium opacity-75">플랫폼</dt>
                  <dd className="font-mono">
                    {result.platform}{" "}
                    <span className="opacity-60">
                      ({Object.entries(result.platform_scores).map(([k, v]) => `${k}:${v}`).join(", ") || "no signature"})
                    </span>
                  </dd>
                  <dt className="font-medium opacity-75">selector</dt>
                  <dd className="font-mono">{dv.used_selector} <span className="opacity-60">({dv.method})</span></dd>
                  <dt className="font-medium opacity-75">container</dt>
                  <dd className="font-mono">{dv.container_label}</dd>
                  <dt className="font-medium opacity-75">제거된 노이즈 노드</dt>
                  <dd>{dv.stripped_nodes}개</dd>
                </dl>
                {dv.warnings.length > 0 && (
                  <ul className="mt-2 text-xs list-disc pl-5">
                    {dv.warnings.map((w, i) => <li key={i}>{w}</li>)}
                  </ul>
                )}
              </section>
            );
          })()}

          <section>
            <h2 className="text-lg font-semibold mb-2">메타</h2>
            <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
              <dt className="font-medium text-gray-600">title</dt>
              <dd>{result.title}</dd>
              <dt className="font-medium text-gray-600">og:title</dt>
              <dd>{result.meta["og:title"] ?? "-"}</dd>
              <dt className="font-medium text-gray-600">body 길이</dt>
              <dd>{result.body_text_len.toLocaleString()}자</dd>
              <dt className="font-medium text-gray-600">이미지</dt>
              <dd>{result.image_count}장</dd>
            </dl>
          </section>

          <section>
            <h2 className="text-lg font-semibold mb-2">본문 텍스트 (HTML 추출)</h2>
            <pre className="p-3 bg-gray-50 rounded border text-sm whitespace-pre-wrap break-words max-h-72 overflow-y-auto">
              {result.body_text}
            </pre>
          </section>

          <section>
            <h2 className="text-lg font-semibold mb-2">이미지 + OCR 결과</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {result.images.map((img, i) => (
                <div key={i} className="border rounded p-3 space-y-2">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={img.src}
                    alt={img.alt || `image-${i}`}
                    className="w-full h-48 object-contain bg-gray-100 rounded"
                  />
                  <p className="text-xs text-gray-500 break-all">{img.src}</p>
                  <div>
                    <div className="text-xs font-medium text-gray-600 mb-1">OCR 결과</div>
                    {img.error ? (
                      <p className="text-xs text-red-700">{img.error}</p>
                    ) : img.ocr_text ? (
                      <pre className="text-xs whitespace-pre-wrap p-2 bg-yellow-50 rounded max-h-32 overflow-y-auto">
                        {img.ocr_text}
                      </pre>
                    ) : (
                      <p className="text-xs text-gray-400 italic">(추출된 텍스트 없음)</p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </section>
        </article>
      )}
    </main>
  );
}
