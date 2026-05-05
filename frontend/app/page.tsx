"use client";

import { useState, useRef, useEffect } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8090";

type Product = {
  id: number;
  name: string;
  brand: string;
  category: string;
  subcategory: string;
  gender: string;
  season: string;
  style: string;
  color: string;
  price: number;
  salePrice: number | null;
  stock: number;
  description: string;
  distance: number;
};

type ParsedQuery = {
  filters: Record<string, unknown>;
  semanticQuery: string;
};

type RecommendResult = {
  parsed: ParsedQuery;
  candidateCount: number;
  recommendations: { productId: number; reason: string }[];
  candidatesPreview: Product[];
};

type Message =
  | { role: "user"; text: string }
  | { role: "assistant"; mode: "recommend"; data: RecommendResult; products: Product[] }
  | { role: "assistant"; mode: "search"; data: Product[] }
  | { role: "assistant"; mode: "error"; text: string };

const SAMPLE_QUERIES = [
  "봄에 입을 여성 캐주얼 원피스 5만원 이하",
  "오피스에 입고 갈 깔끔한 블레이저",
  "겨울에 따뜻하게 입을 두꺼운 코트",
  "남자 검정 슬림한 청바지",
  "데일리로 신을 흰색 스니커즈",
];

export default function Home() {
  const [mode, setMode] = useState<"recommend" | "search">("recommend");
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  async function send(query: string) {
    const trimmed = query.trim();
    if (!trimmed || loading) return;

    setMessages((m) => [...m, { role: "user", text: trimmed }]);
    setInput("");
    setLoading(true);

    try {
      if (mode === "recommend") {
        const res = await fetch(`${API_BASE}/recommend`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ query: trimmed, topCandidates: 30 }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
        const data: RecommendResult = await res.json();
        const byId = new Map(data.candidatesPreview.map((p) => [p.id, p]));
        const products = data.recommendations
          .map((r) => byId.get(r.productId))
          .filter((p): p is Product => Boolean(p));
        setMessages((m) => [...m, { role: "assistant", mode: "recommend", data, products }]);
      } else {
        const res = await fetch(`${API_BASE}/search`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ query: trimmed, k: 10 }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
        const data: Product[] = await res.json();
        setMessages((m) => [...m, { role: "assistant", mode: "search", data }]);
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setMessages((m) => [...m, { role: "assistant", mode: "error", text: msg }]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100 flex flex-col">
      <header className="border-b border-neutral-800 px-6 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">의류 추천 데모</h1>
          <p className="text-xs text-neutral-400">
            Postgres + pgvector + bge-m3 (1024d) + Claude — 자연어로 검색해보세요
          </p>
        </div>
        <div className="flex items-center gap-2 text-sm">
          <span className="text-neutral-400">모드:</span>
          <button
            onClick={() => setMode("recommend")}
            className={`px-3 py-1 rounded-full border transition ${
              mode === "recommend"
                ? "bg-indigo-600 border-indigo-500 text-white"
                : "border-neutral-700 text-neutral-300 hover:bg-neutral-800"
            }`}
          >
            풀 추천 (LLM)
          </button>
          <button
            onClick={() => setMode("search")}
            className={`px-3 py-1 rounded-full border transition ${
              mode === "search"
                ? "bg-indigo-600 border-indigo-500 text-white"
                : "border-neutral-700 text-neutral-300 hover:bg-neutral-800"
            }`}
          >
            벡터 검색만
          </button>
        </div>
      </header>

      <main className="flex-1 overflow-y-auto px-4 py-6 max-w-4xl mx-auto w-full">
        {messages.length === 0 && (
          <div className="text-center text-neutral-400 mt-12 space-y-4">
            <p>아래 예시로 시작해보세요</p>
            <div className="flex flex-wrap gap-2 justify-center">
              {SAMPLE_QUERIES.map((q) => (
                <button
                  key={q}
                  onClick={() => send(q)}
                  className="px-3 py-1.5 text-sm rounded-full border border-neutral-700 hover:bg-neutral-800"
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="space-y-6">
          {messages.map((m, i) => (
            <MessageBubble key={i} m={m} />
          ))}
          {loading && (
            <div className="text-neutral-400 text-sm flex items-center gap-2">
              <span className="inline-block w-2 h-2 rounded-full bg-indigo-400 animate-pulse" />
              {mode === "recommend"
                ? "LLM 파싱 → 임베딩 → 검색 → LLM 리랭크 중…"
                : "임베딩 → 벡터 검색 중…"}
            </div>
          )}
          <div ref={bottomRef} />
        </div>
      </main>

      <footer className="border-t border-neutral-800 p-4">
        <form
          className="max-w-4xl mx-auto flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            send(input);
          }}
        >
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="찾고 싶은 상품을 자연어로 입력 (예: 봄 캐주얼 원피스 5만원 이하)"
            className="flex-1 px-4 py-3 rounded-lg bg-neutral-900 border border-neutral-700 focus:border-indigo-500 focus:outline-none"
            disabled={loading}
          />
          <button
            type="submit"
            disabled={loading || !input.trim()}
            className="px-5 py-3 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:bg-neutral-700 disabled:text-neutral-500"
          >
            보내기
          </button>
        </form>
      </footer>
    </div>
  );
}

function MessageBubble({ m }: { m: Message }) {
  if (m.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="bg-indigo-600 px-4 py-2 rounded-2xl max-w-[75%]">{m.text}</div>
      </div>
    );
  }

  if (m.mode === "error") {
    return (
      <div className="bg-red-950 border border-red-800 px-4 py-3 rounded-lg text-red-200 text-sm">
        <div className="font-semibold mb-1">에러</div>
        <pre className="whitespace-pre-wrap">{m.text}</pre>
        <p className="mt-2 text-xs text-red-300">
          /recommend 호출은 ANTHROPIC_API_KEY가 필요합니다. 키 없이 보려면 우측 상단 &quot;벡터 검색만&quot; 모드를 선택하세요.
        </p>
      </div>
    );
  }

  if (m.mode === "search") {
    return (
      <div className="space-y-3">
        <div className="text-xs text-neutral-400">
          벡터 검색 결과 ({m.data.length}개) — 거리가 낮을수록 유사도 ↑
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {m.data.map((p) => (
            <ProductCard key={p.id} p={p} />
          ))}
        </div>
      </div>
    );
  }

  // recommend
  return (
    <div className="space-y-4">
      <div className="bg-neutral-900 border border-neutral-800 rounded-lg p-4 text-sm">
        <div className="text-xs text-neutral-400 mb-2">LLM 파싱 결과</div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          {Object.entries(m.data.parsed.filters).map(([k, v]) => (
            <div key={k} className="bg-neutral-800 px-2 py-1 rounded text-xs">
              <span className="text-neutral-500">{k}:</span> {String(v)}
            </div>
          ))}
        </div>
        <div className="mt-2 text-xs text-neutral-400">
          semantic: <span className="text-neutral-200">{m.data.parsed.semanticQuery}</span> ·
          후보 {m.data.candidateCount}개에서 추천 {m.data.recommendations.length}개 선정
        </div>
      </div>

      <div className="space-y-3">
        {m.data.recommendations.map((rec, idx) => {
          const product = m.products.find((p) => p.id === rec.productId);
          return (
            <div
              key={idx}
              className="border border-indigo-900 bg-indigo-950/30 rounded-lg p-4 space-y-2"
            >
              <div className="flex items-baseline gap-2">
                <span className="text-indigo-400 font-bold">#{idx + 1}</span>
                <span className="font-semibold">{product?.name ?? `상품 ${rec.productId}`}</span>
              </div>
              <div className="text-sm text-neutral-300">{rec.reason}</div>
              {product && <ProductCard p={product} />}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ProductCard({ p }: { p: Product }) {
  const finalPrice = p.salePrice ?? p.price;
  return (
    <div className="bg-neutral-900 border border-neutral-800 rounded-lg p-3 text-sm">
      <div className="flex justify-between items-start gap-2">
        <div className="font-medium">{p.name}</div>
        <div className="text-right">
          {p.salePrice && (
            <div className="text-xs text-neutral-500 line-through">
              {p.price.toLocaleString()}원
            </div>
          )}
          <div className="text-indigo-300 font-semibold">{finalPrice.toLocaleString()}원</div>
        </div>
      </div>
      <div className="mt-1 text-xs text-neutral-400 flex flex-wrap gap-x-2">
        <span>
          {p.category}/{p.subcategory}
        </span>
        <span>·</span>
        <span>{p.gender}</span>
        <span>·</span>
        <span>{p.season}</span>
        <span>·</span>
        <span>{p.style}</span>
        <span>·</span>
        <span>{p.color}</span>
        <span>·</span>
        <span className={p.stock > 0 ? "text-emerald-400" : "text-red-400"}>
          재고 {p.stock}
        </span>
      </div>
      {typeof p.distance === "number" && (
        <div className="mt-1 text-xs text-neutral-500">distance: {p.distance.toFixed(3)}</div>
      )}
    </div>
  );
}
