"use client";

import { useEffect, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8090";

type Advertiser = {
  id: number;
  name: string;
  hostType: string;
  shopUrl: string;
  shopKey: string | null;
  licenseKey: string | null;
  notes: string | null;
  selectorName: string | null;
  selectorDetail: string | null;
  selectorPrice: string | null;
  imageAttrs: string | null;
  detailAnchorStart: string | null;
  detailAnchorEnd: string | null;
  createdAt: string;
  updatedAt: string;
};

type SyncResult = {
  advertiserId: number;
  totalCount: number;
  savedCount: number;
  pagesProcessed: number;
  status: string;
  error: string | null;
};

type Sel = {
  id?: number;
  role: "name" | "detail" | "price";
  selector: string;
  priority: number;
  enabled: boolean;
};

type ImageBlock = {
  id?: number;
  pattern: string;
  enabled: boolean;
  notes?: string | null;
};

const HOST_TYPES = ["makeshop", "cafe24", "godo", "imweb", "custom"] as const;

export default function SyncPage() {
  const [advertisers, setAdvertisers] = useState<Advertiser[]>([]);
  const [form, setForm] = useState({
    name: "",
    hostType: "makeshop",
    shopUrl: "",
    shopKey: "",
    licenseKey: "",
    notes: "",
    selectorName: "",
    selectorDetail: "",
    selectorPrice: "",
    imageAttrs: "",
  });
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<Record<number, SyncResult>>({});
  const [editingId, setEditingId] = useState<number | null>(null);
  const [selectors, setSelectors] = useState<Sel[]>([]);
  const [selectorsByAdv, setSelectorsByAdv] = useState<Record<number, Sel[]>>({});
  const [imageBlocks, setImageBlocks] = useState<ImageBlock[]>([]);

  const isEditing = editingId !== null;
  const emptyForm = { name: "", hostType: "makeshop", shopUrl: "", shopKey: "", licenseKey: "", notes: "", selectorName: "", selectorDetail: "", selectorPrice: "", imageAttrs: "", detailAnchorStart: "", detailAnchorEnd: "" };

  async function startEdit(a: Advertiser) {
    setEditingId(a.id);
    setForm({
      name: a.name,
      hostType: a.hostType,
      shopUrl: a.shopUrl,
      shopKey: a.shopKey ?? "",
      licenseKey: a.licenseKey ?? "",
      notes: a.notes ?? "",
      selectorName: a.selectorName ?? "",
      selectorDetail: a.selectorDetail ?? "",
      selectorPrice: a.selectorPrice ?? "",
      imageAttrs: a.imageAttrs ?? "",
      detailAnchorStart: a.detailAnchorStart ?? "",
      detailAnchorEnd: a.detailAnchorEnd ?? "",
    });
    setError(null);
    // selectors + image blocks 동시 로드
    try {
      const [rsSel, rsBlk] = await Promise.all([
        fetch(`${API_BASE}/advertisers/${a.id}/selectors`),
        fetch(`${API_BASE}/advertisers/${a.id}/image-blocks`),
      ]);
      if (rsSel.ok) setSelectors(await rsSel.json() as Sel[]);
      if (rsBlk.ok) setImageBlocks(await rsBlk.json() as ImageBlock[]);
    } catch {}
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function cancelEdit() {
    setEditingId(null);
    setForm(emptyForm);
    setSelectors([]);
    setImageBlocks([]);
    setError(null);
  }

  function addBlock() {
    setImageBlocks((arr) => [...arr, { pattern: "", enabled: true }]);
  }
  function updateBlock(idx: number, patch: Partial<ImageBlock>) {
    setImageBlocks((arr) => arr.map((b, i) => i === idx ? { ...b, ...patch } : b));
  }
  function removeBlock(idx: number) {
    setImageBlocks((arr) => arr.filter((_, i) => i !== idx));
  }

  function addSelector(role: Sel["role"]) {
    setSelectors((arr) => [...arr, { role, selector: "", priority: 100, enabled: true }]);
  }
  function updateSelector(idx: number, patch: Partial<Sel>) {
    setSelectors((arr) => arr.map((s, i) => i === idx ? { ...s, ...patch } : s));
  }
  function removeSelector(idx: number) {
    setSelectors((arr) => arr.filter((_, i) => i !== idx));
  }

  async function load() {
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/advertisers`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const list = await res.json() as Advertiser[];
      setAdvertisers(list);
      // 광고주별 selector 동시 fetch (N+1 — 광고주 수 작을 때만 OK)
      const pairs = await Promise.all(list.map(async (a) => {
        try {
          const r = await fetch(`${API_BASE}/advertisers/${a.id}/selectors`);
          return [a.id, r.ok ? (await r.json()) as Sel[] : []] as const;
        } catch {
          return [a.id, []] as const;
        }
      }));
      setSelectorsByAdv(Object.fromEntries(pairs));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(isEditing ? "update" : "create");
    setError(null);
    try {
      const url = isEditing ? `${API_BASE}/advertisers/${editingId}` : `${API_BASE}/advertisers`;
      const method = isEditing ? "PUT" : "POST";
      const res = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
      const saved = await res.json() as Advertiser;
      const id = saved.id;
      // selectors + image blocks 통째로 교체
      const cleanedSels = selectors.filter(s => s.selector.trim());
      const cleanedBlks = imageBlocks.filter(b => b.pattern.trim());
      await Promise.all([
        fetch(`${API_BASE}/advertisers/${id}/selectors`, {
          method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ items: cleanedSels }),
        }),
        fetch(`${API_BASE}/advertisers/${id}/image-blocks`, {
          method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ items: cleanedBlks }),
        }),
      ]);
      setForm(emptyForm);
      setSelectors([]);
      setImageBlocks([]);
      setEditingId(null);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function runSync(id: number) {
    setBusy(`sync-${id}`);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/advertisers/${id}/sync`, { method: "POST" });
      const text = await res.text();
      if (!res.ok) throw new Error(`HTTP ${res.status}: ${text.slice(0, 400)}`);
      const data = JSON.parse(text) as SyncResult;
      setResults((r) => ({ ...r, [id]: data }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function remove(id: number) {
    if (!confirm("삭제할까요?")) return;
    setBusy(`del-${id}`);
    try {
      await fetch(`${API_BASE}/advertisers/${id}`, { method: "DELETE" });
      await load();
    } finally {
      setBusy(null);
    }
  }

  return (
    <main className="mx-auto max-w-5xl p-8 font-sans">
      <header className="mb-6">
        <h1 className="text-2xl font-bold">광고주 상품 동기화</h1>
        <p className="text-sm text-gray-600 mt-1">
          메이크샵 Shopkey / Licensekey 입력 → 500개씩 페이지네이션으로 상품을 rv_products에 저장.
        </p>
      </header>

      <section className="mb-8 p-4 border rounded-lg bg-gray-50">
        <h2 className="font-semibold mb-3 flex items-center gap-2">
          {isEditing ? <>광고주 수정 <span className="text-xs font-mono px-1.5 py-0.5 rounded bg-amber-200 text-amber-900">#{editingId}</span></> : "광고주 등록"}
          {isEditing && (
            <button type="button" onClick={cancelEdit} className="ml-auto text-xs text-gray-600 underline">취소 (신규 등록 모드로)</button>
          )}
        </h2>
        <form onSubmit={submit} className="grid grid-cols-2 gap-3">
          <label className="text-sm">
            상호명
            <input
              required
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              className="mt-1 block w-full rounded border-gray-300 px-3 py-2"
              placeholder="(예) 피핀"
            />
          </label>
          <label className="text-sm">
            호스팅
            <select
              value={form.hostType}
              onChange={(e) => setForm({ ...form, hostType: e.target.value })}
              className="mt-1 block w-full rounded border-gray-300 px-3 py-2"
            >
              {HOST_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </label>
          <label className="text-sm col-span-2">
            shopUrl
            <input
              required
              type="url"
              value={form.shopUrl}
              onChange={(e) => setForm({ ...form, shopUrl: e.target.value })}
              className="mt-1 block w-full rounded border-gray-300 px-3 py-2 font-mono text-xs"
              placeholder="https://www.pippin.co.kr"
            />
          </label>
          <label className="text-sm">
            Shopkey
            <input
              value={form.shopKey}
              onChange={(e) => setForm({ ...form, shopKey: e.target.value })}
              className="mt-1 block w-full rounded border-gray-300 px-3 py-2 font-mono text-xs"
              placeholder="makeshop shopkey"
            />
          </label>
          <label className="text-sm">
            Licensekey
            <input
              value={form.licenseKey}
              onChange={(e) => setForm({ ...form, licenseKey: e.target.value })}
              className="mt-1 block w-full rounded border-gray-300 px-3 py-2 font-mono text-xs"
              placeholder="makeshop licensekey"
            />
          </label>
          <label className="text-sm col-span-2">
            메모
            <input
              value={form.notes}
              onChange={(e) => setForm({ ...form, notes: e.target.value })}
              className="mt-1 block w-full rounded border-gray-300 px-3 py-2"
            />
          </label>

          <details open className="col-span-2 mt-2 text-sm">
            <summary className="cursor-pointer text-gray-700 select-none font-medium">스크랩핑 셀렉터 (광고주별 1:N)</summary>
            <div className="mt-2 p-3 bg-white rounded border space-y-4">
              {(["name", "detail", "price"] as const).map((role) => {
                const items = selectors.filter(s => s.role === role);
                const labels = { name: "상품명 / 옵션", detail: "상품 상세 본문", price: "가격" }[role];
                const placeholder = { name: ".info, .product-title", detail: "#productDetail, #dd01", price: ".price" }[role];
                return (
                  <div key={role}>
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs font-medium text-gray-700">{labels} ({items.length}개)</span>
                      <button type="button" onClick={() => addSelector(role)} className="text-xs px-2 py-0.5 rounded border border-gray-300 hover:bg-gray-100">+ 추가</button>
                    </div>
                    <ul className="space-y-1">
                      {selectors.map((s, idx) => s.role !== role ? null : (
                        <li key={idx} className="flex items-center gap-1.5">
                          <input
                            value={s.selector}
                            onChange={(e) => updateSelector(idx, { selector: e.target.value })}
                            className="flex-1 rounded border-gray-300 px-2 py-1 font-mono text-xs"
                            placeholder={placeholder}
                          />
                          <input
                            type="number"
                            value={s.priority}
                            onChange={(e) => updateSelector(idx, { priority: Number(e.target.value) })}
                            className="w-16 rounded border-gray-300 px-2 py-1 text-xs"
                            title="priority (낮을수록 먼저 시도)"
                          />
                          <label className="flex items-center gap-1 text-xs">
                            <input
                              type="checkbox"
                              checked={s.enabled}
                              onChange={(e) => updateSelector(idx, { enabled: e.target.checked })}
                            />
                            on
                          </label>
                          <button type="button" onClick={() => removeSelector(idx)} className="text-xs px-2 py-1 text-red-600">✕</button>
                        </li>
                      ))}
                      {items.length === 0 && (
                        <li className="text-xs text-gray-400 italic">없음 (플랫폼 디폴트 폴백)</li>
                      )}
                    </ul>
                  </div>
                );
              })}

              <div>
                <label>
                  <span className="text-xs font-medium text-gray-700">이미지 lazy 속성 (콤마구분, 광고주 1개)</span>
                  <input
                    value={form.imageAttrs}
                    onChange={(e) => setForm({ ...form, imageAttrs: e.target.value })}
                    className="mt-1 block w-full rounded border-gray-300 px-2 py-1 font-mono text-xs"
                    placeholder="data-frz-src,data-src,src"
                  />
                </label>
              </div>

              <div className="pt-2 border-t">
                <span className="text-xs font-medium text-gray-700">HTML anchor (주석/문자열로 본문 영역 슬라이스) <span className="text-gray-500">— 최우선 적용</span></span>
                <div className="mt-1 grid grid-cols-2 gap-2">
                  <label>
                    <span className="text-xs text-gray-600">시작 anchor</span>
                    <input
                      value={form.detailAnchorStart}
                      onChange={(e) => setForm({ ...form, detailAnchorStart: e.target.value })}
                      className="mt-1 block w-full rounded border-gray-300 px-2 py-1 font-mono text-xs"
                      placeholder="<!--//sns상품배너끝-->"
                    />
                  </label>
                  <label>
                    <span className="text-xs text-gray-600">끝 anchor</span>
                    <input
                      value={form.detailAnchorEnd}
                      onChange={(e) => setForm({ ...form, detailAnchorEnd: e.target.value })}
                      className="mt-1 block w-full rounded border-gray-300 px-2 py-1 font-mono text-xs"
                      placeholder="<!-- //content -->"
                    />
                  </label>
                </div>
              </div>

              <p className="text-xs text-gray-500">우선순위: anchor → selector_detail (priority 순) → 플랫폼 디폴트({"{makeshop=#productDetail, cafe24=#prdDetail}"}).</p>

              <div className="pt-3 border-t">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs font-medium text-gray-700">이미지 차단 URL / 패턴 ({imageBlocks.length}개)</span>
                  <button type="button" onClick={addBlock} className="text-xs px-2 py-0.5 rounded border border-gray-300 hover:bg-gray-100">+ 추가</button>
                </div>
                <p className="mb-2 text-xs text-gray-500">정확한 URL을 박으면 그 URL만 차단. 짧은 substring(예: <span className="font-mono">/event/</span>)을 박으면 그게 포함된 모든 URL 차단.</p>
                <ul className="space-y-1">
                  {imageBlocks.map((b, idx) => (
                    <li key={idx} className="flex items-center gap-1.5">
                      <input
                        value={b.pattern}
                        onChange={(e) => updateBlock(idx, { pattern: e.target.value })}
                        className="flex-1 rounded border-gray-300 px-2 py-1 font-mono text-xs"
                        placeholder="/event/ 또는 _bnr 또는 board/"
                      />
                      <input
                        value={b.notes ?? ""}
                        onChange={(e) => updateBlock(idx, { notes: e.target.value })}
                        className="w-32 rounded border-gray-300 px-2 py-1 text-xs"
                        placeholder="메모(선택)"
                      />
                      <label className="flex items-center gap-1 text-xs">
                        <input
                          type="checkbox"
                          checked={b.enabled}
                          onChange={(e) => updateBlock(idx, { enabled: e.target.checked })}
                        />
                        on
                      </label>
                      <button type="button" onClick={() => removeBlock(idx)} className="text-xs px-2 py-1 text-red-600">✕</button>
                    </li>
                  ))}
                  {imageBlocks.length === 0 && <li className="text-xs text-gray-400 italic">없음 (모든 이미지 다운로드)</li>}
                </ul>
                <p className="mt-1 text-xs text-gray-500">예시: <span className="font-mono">/event/, _bnr, member_benefit, /board/, /shopimages/luvre/, /luvre/2024/10/ea/2166</span></p>
              </div>
            </div>
          </details>

          <div className="col-span-2">
            <button
              disabled={busy === "create" || busy === "update"}
              className="px-4 py-2 rounded bg-black text-white text-sm disabled:opacity-50"
            >
              {busy === "create" ? "등록 중..." : busy === "update" ? "수정 중..." : isEditing ? "수정 저장" : "등록"}
            </button>
          </div>
        </form>
      </section>

      {error && (
        <div className="p-3 mb-4 rounded border border-red-300 bg-red-50 text-red-800 text-sm whitespace-pre-wrap">
          {error}
        </div>
      )}

      <section>
        <h2 className="font-semibold mb-3">등록된 광고주 ({advertisers.length})</h2>
        {advertisers.length === 0 && <p className="text-sm text-gray-500">없음</p>}
        <ul className="space-y-3">
          {advertisers.map((a) => {
            const r = results[a.id];
            const syncing = busy === `sync-${a.id}`;
            return (
              <li key={a.id} className="p-4 border rounded">
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="font-semibold">{a.name}</span>
                      <span className="text-xs font-mono px-1.5 py-0.5 rounded bg-gray-200">{a.hostType}</span>
                      <span className="text-xs text-gray-500">#{a.id}</span>
                    </div>
                    <div className="mt-1 text-xs text-gray-600 truncate font-mono">{a.shopUrl}</div>
                    <div className="mt-1 text-xs text-gray-500 font-mono">
                      shopKey: <span className="text-gray-700">{mask(a.shopKey)}</span>
                      {"  ·  "}
                      licenseKey: <span className="text-gray-700">{mask(a.licenseKey)}</span>
                    </div>
                    {(() => {
                      const sels = selectorsByAdv[a.id] ?? [];
                      const byRole = { name: [] as Sel[], detail: [] as Sel[], price: [] as Sel[] };
                      for (const s of sels) (byRole[s.role] ??= []).push(s);
                      const total = sels.length;
                      if (total === 0) return (
                        <div className="mt-1 text-xs text-gray-400 italic">selector 없음 (플랫폼 디폴트 폴백)</div>
                      );
                      return (
                        <div className="mt-1 text-xs text-gray-500 font-mono space-y-0.5">
                          {byRole.name.length > 0 && <div>name [{byRole.name.length}]: <span className="text-gray-700">{byRole.name.map(s => s.selector).join(", ")}</span></div>}
                          {byRole.detail.length > 0 && <div>detail [{byRole.detail.length}]: <span className="text-gray-700">{byRole.detail.map(s => s.selector).join(", ")}</span></div>}
                          {byRole.price.length > 0 && <div>price [{byRole.price.length}]: <span className="text-gray-700">{byRole.price.map(s => s.selector).join(", ")}</span></div>}
                        </div>
                      );
                    })()}
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <a
                      href={`/sync/${a.id}/products`}
                      className="px-3 py-1.5 rounded border text-xs"
                    >
                      상품 보기
                    </a>
                    <button
                      onClick={() => startEdit(a)}
                      disabled={busy !== null}
                      className="px-3 py-1.5 rounded border border-amber-400 text-amber-700 text-xs disabled:opacity-50"
                    >
                      수정
                    </button>
                    <button
                      onClick={() => runSync(a.id)}
                      disabled={syncing}
                      className="px-3 py-1.5 rounded bg-blue-600 text-white text-xs disabled:opacity-50"
                    >
                      {syncing ? "동기화 중..." : "동기화"}
                    </button>
                    <button
                      onClick={() => remove(a.id)}
                      disabled={busy !== null}
                      className="px-3 py-1.5 rounded border border-red-300 text-red-700 text-xs disabled:opacity-50"
                    >
                      삭제
                    </button>
                  </div>
                </div>
                {r && (
                  <div className={`mt-3 p-2 rounded text-xs ${r.status === "COMPLETED" ? "bg-green-50 text-green-900" : "bg-orange-50 text-orange-900"}`}>
                    <strong>{r.status}</strong> · total={r.totalCount} · saved={r.savedCount} · pages={r.pagesProcessed}
                    {r.error && <div className="mt-1 font-mono">{r.error}</div>}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      </section>
    </main>
  );
}

function mask(v: string | null): string {
  if (!v) return "(없음)";
  if (v.length <= 6) return "*".repeat(v.length);
  return v.slice(0, 3) + "…" + v.slice(-3);
}
