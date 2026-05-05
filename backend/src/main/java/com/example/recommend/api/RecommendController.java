package com.example.recommend.api;

import com.example.recommend.embed.EmbedderClient;
import com.example.recommend.search.Product;
import com.example.recommend.search.ProductSearchRepository;
import com.example.recommend.search.RecommendService;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

@RestController
@RequestMapping
public class RecommendController {

    public record RecommendRequest(@NotBlank String query, @Min(1) Integer topCandidates) {}

    public record SearchRequest(@NotBlank String query, Map<String, Object> filters, Integer k) {}

    private final RecommendService service;
    private final EmbedderClient embedder;
    private final ProductSearchRepository repo;

    public RecommendController(RecommendService service,
                               EmbedderClient embedder,
                               ProductSearchRepository repo) {
        this.service = service;
        this.embedder = embedder;
        this.repo = repo;
    }

    /** 풀 파이프라인: LLM 파싱 + 임베딩 + 검색 + LLM rerank. */
    @PostMapping("/recommend")
    public RecommendService.Result recommend(@RequestBody RecommendRequest req) {
        int k = (req.topCandidates() == null) ? 30 : req.topCandidates();
        return service.recommend(req.query(), k);
    }

    /** 디버그: LLM 없이 임베딩 + SQL 검색만. ANTHROPIC_API_KEY 없이도 동작. */
    @PostMapping("/search")
    public List<Product> search(@RequestBody SearchRequest req) {
        float[] vec = embedder.embed(req.query());
        Map<String, Object> filters = req.filters() == null ? Map.of() : req.filters();
        int k = req.k() == null ? 10 : req.k();
        return repo.search(filters, vec, k);
    }

    @GetMapping("/healthz")
    public java.util.Map<String, Object> health() {
        return java.util.Map.of("ok", true);
    }
}
