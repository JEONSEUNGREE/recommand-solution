import sys
sys.path.insert(0, ".")

from embedder.search_rv import _LLM_PARSE_PROMPT

# 프롬프트 길이 측정
chars = len(_LLM_PARSE_PROMPT)
lines = _LLM_PARSE_PROMPT.count('\n')

# tiktoken으로 정확한 토큰 수 계산
try:
    import tiktoken
    enc4o = tiktoken.encoding_for_model("gpt-4o-mini")
    tokens_4o = len(enc4o.encode(_LLM_PARSE_PROMPT))
    print(f"system prompt chars  : {chars:,}")
    print(f"system prompt lines  : {lines:,}")
    print(f"system prompt tokens (gpt-4o-mini): {tokens_4o:,}")

    # 예시 쿼리들 토큰 측정
    queries = [
        "귀걸이",
        "봄 데이트 귀걸이",
        "비싸보이지만 저렴한 귀걸이 추천",
        "손예진 착용 14k 로즈골드 귀걸이",
        "30대 워킹맘이 출근할때 입을 미니멀 블랙 바람막이",
    ]
    print()
    print("=== 예시 쿼리별 input 토큰 ===")
    for q in queries:
        q_tok = len(enc4o.encode(q))
        total_in = tokens_4o + q_tok
        # gpt-4o-mini 요금: input $0.15/MTok, output $0.60/MTok
        # 예상 output ~200tok
        est_out = 200
        cost = total_in * 0.00000015 + est_out * 0.0000006
        print(f"  query={q_tok:>3}tok  total_in={total_in:>4}tok  est_out≈{est_out}tok  cost≈${cost:.6f}  | {q}")

    print()
    print(f"=== OpenAI embedding (text-embedding-3-small) ===")
    sample = "봄 데이트 14k 귀걸이"
    emb_tok = len(enc4o.encode(sample))
    emb_cost = emb_tok * 0.00000002  # $0.02/MTok
    print(f"  query tokens: {emb_tok}")
    print(f"  cost per search: ${emb_cost:.8f}")

except ImportError:
    print(f"system prompt chars  : {chars:,}")
    print(f"system prompt lines  : {lines:,}")
    print("tiktoken 없음 — 대략 chars/3.5 로 추정")
    est = chars // 3
    print(f"system prompt tokens (추정): ~{est:,}")
