package com.example.recommend.search;

import com.example.recommend.embed.EmbedderClient;
import org.springframework.stereotype.Service;

import java.util.List;

@Service
public class RecommendService {

    public record Result(
            ParsedQuery parsed,
            int candidateCount,
            List<Reranker.Recommendation> recommendations,
            List<Product> candidatesPreview
    ) {}

    private final QueryParser parser;
    private final EmbedderClient embedder;
    private final ProductSearchRepository repo;
    private final Reranker reranker;

    public RecommendService(QueryParser parser,
                            EmbedderClient embedder,
                            ProductSearchRepository repo,
                            Reranker reranker) {
        this.parser = parser;
        this.embedder = embedder;
        this.repo = repo;
        this.reranker = reranker;
    }

    public Result recommend(String userText, int topCandidates) {
        // 1) LLM 자연어 → 구조화 JSON
        ParsedQuery parsed = parser.parse(userText);

        // 2) 임베딩 모델: semantic_query → vector
        float[] vec = embedder.embed(parsed.semanticQuery());

        // 3) Postgres + pgvector: 필터 + 거리 정렬
        List<Product> candidates = repo.search(parsed.filters(), vec, topCandidates);

        // 4) LLM rerank + 추천 이유
        List<Reranker.Recommendation> recs = reranker.rerank(userText, candidates);

        List<Product> preview = candidates.size() > 5 ? candidates.subList(0, 5) : candidates;
        return new Result(parsed, candidates.size(), recs, preview);
    }
}
