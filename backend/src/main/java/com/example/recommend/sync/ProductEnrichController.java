package com.example.recommend.sync;

import com.fasterxml.jackson.databind.JsonNode;
import org.springframework.web.bind.annotation.*;

import java.io.IOException;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/advertisers/{advertiserId}/products")
public class ProductEnrichController {

    private final RvProductRepository products;
    private final ProductEnrichService service;
    private final org.springframework.jdbc.core.JdbcTemplate jdbc;

    public ProductEnrichController(RvProductRepository products,
                                    ProductEnrichService service,
                                    org.springframework.jdbc.core.JdbcTemplate jdbc) {
        this.products = products;
        this.service = service;
        this.jdbc = jdbc;
    }

    @GetMapping
    public Map<String, Object> list(@PathVariable long advertiserId,
                                    @RequestParam(defaultValue = "0") int page,
                                    @RequestParam(defaultValue = "30") int size,
                                    @RequestParam(required = false) String filter) {
        long total = products.count(advertiserId, filter);
        List<RvProduct> rows = products.page(advertiserId, size, page * size, filter);

        // 현재 페이지 rv_product_id 중 product_enriched / embedded 상태인 ID set
        List<Long> pageIds = rows.stream().map(RvProduct::id).toList();
        java.util.Set<Long> enrichedIds = java.util.Set.copyOf(
                pageIds.isEmpty() ? java.util.List.<Long>of() :
                jdbc.queryForList(
                        "SELECT rv_product_id FROM product_enriched WHERE rv_product_id IN (" +
                                String.join(",", java.util.Collections.nCopies(pageIds.size(), "?")) + ")",
                        Long.class, pageIds.toArray())
        );
        java.util.Set<Long> embeddedIds = java.util.Set.copyOf(
                pageIds.isEmpty() ? java.util.List.<Long>of() :
                jdbc.queryForList(
                        "SELECT rv_product_id FROM product_enriched WHERE enrich_status='embedded' AND rv_product_id IN (" +
                                String.join(",", java.util.Collections.nCopies(pageIds.size(), "?")) + ")",
                        Long.class, pageIds.toArray())
        );

        // 광고주 전체 카운트
        Long enrichedTotal = jdbc.queryForObject(
                "SELECT COUNT(*) FROM product_enriched WHERE advertiser_id=?", Long.class, advertiserId);
        Long embeddedTotal = jdbc.queryForObject(
                "SELECT COUNT(*) FROM product_enriched WHERE advertiser_id=? AND enrich_status='embedded'",
                Long.class, advertiserId);

        Map<String, Long> counts = Map.of(
                "total", products.count(advertiserId, null),
                "pending", products.count(advertiserId, "to_scrape"),
                "scraped_pending_enrich", products.count(advertiserId, "to_enrich"),
                "done", products.count(advertiserId, "done"),
                "llm_enriched", enrichedTotal == null ? 0L : enrichedTotal,
                "vectorized", embeddedTotal == null ? 0L : embeddedTotal
        );
        return Map.of(
                "page", page,
                "size", size,
                "total", total,
                "filter", filter == null ? "" : filter,
                "items", rows,
                "counts", counts,
                "enrichedIds", enrichedIds,
                "embeddedIds", embeddedIds
        );
    }

    @PostMapping("/{code}/scrape")
    public JsonNode scrape(@PathVariable long advertiserId, @PathVariable("code") String code) throws IOException {
        return service.scrape(advertiserId, code);
    }

    /**
     * 미스크랩 상품을 N개 워커 병렬로 fetch. 백그라운드로 실행하고 즉시 202 응답.
     * mode=page  → 기존 방식 (광고주 사이트의 상품 페이지를 HTML로 다운로드)
     * mode=api   → 메이크샵 OpenAPI product_content 배치 호출 (4~5배 빠름)
     */
    @PostMapping("/batch-fetch")
    public Map<String, Object> batchFetch(@PathVariable long advertiserId,
                                            @RequestParam(defaultValue = "500") int count,
                                            @RequestParam(defaultValue = "1000") long intervalMs,
                                            @RequestParam(defaultValue = "4") int concurrency,
                                            @RequestParam(defaultValue = "page") String mode,
                                            @RequestParam(defaultValue = "100") int batchSize) {
        ProductEnrichService.BatchProgress p = service.getBatchProgress(advertiserId);
        if ("RUNNING".equals(p.status)) {
            return Map.of("status", "already_running", "advertiserId", advertiserId,
                    "done", p.done, "total", p.total);
        }
        Thread t = new Thread(() -> {
            try {
                if ("api".equalsIgnoreCase(mode)) {
                    service.batchFetchApi(advertiserId, count, intervalMs, concurrency, batchSize);
                } else {
                    service.batchFetch(advertiserId, count, intervalMs, concurrency);
                }
            } catch (Throwable th) {
                org.slf4j.LoggerFactory.getLogger(ProductEnrichController.class)
                        .error("[BatchFetch] thread crashed: {}", th.getMessage(), th);
                p.status = "FAILED";
                p.lastError = th.getMessage();
            }
        }, "batch-fetch-" + advertiserId);
        t.setDaemon(true);
        t.start();
        return Map.of("status", "started", "advertiserId", advertiserId,
                "count", count, "intervalMs", intervalMs, "concurrency", concurrency,
                "mode", mode, "batchSize", batchSize);
    }

    @GetMapping("/batch-fetch/status")
    public Map<String, Object> batchStatus(@PathVariable long advertiserId) {
        ProductEnrichService.BatchProgress p = service.getBatchProgress(advertiserId);
        return Map.of(
                "status", p.status,
                "total", p.total,
                "done", p.done,
                "ok", p.ok,
                "fail", p.fail,
                "currentCode", p.currentCode == null ? "" : p.currentCode,
                "elapsedMs", p.elapsedMs,
                "lastError", p.lastError == null ? "" : p.lastError
        );
    }

    @PostMapping("/batch-fetch/cancel")
    public Map<String, Object> batchCancel(@PathVariable long advertiserId) {
        service.cancelBatch(advertiserId);
        return Map.of("status", "cancel_requested", "advertiserId", advertiserId);
    }

    @PostMapping("/{code}/enrich/ocr")
    public JsonNode enrichOcr(@PathVariable long advertiserId, @PathVariable("code") String code) throws IOException {
        return service.enrichOcr(advertiserId, code);
    }

    @PostMapping("/{code}/enrich/vlm")
    public JsonNode enrichVlm(@PathVariable long advertiserId, @PathVariable("code") String code) throws IOException {
        return service.enrichVlm(advertiserId, code);
    }

    /** 단일 상품 LLM enrich (즉시) — 프론트에서 행별 버튼 호출. force=true 시 기존 enrich 덮어쓰기 (upsert). */
    @PostMapping("/{code}/enrich/llm")
    public JsonNode enrichLlm(@PathVariable long advertiserId,
                                @PathVariable("code") String code,
                                @RequestParam(defaultValue = "haiku") String model,
                                @RequestParam(defaultValue = "10") int imageLimit,
                                @RequestParam(defaultValue = "false") boolean force) throws IOException {
        return service.enrichLlm(advertiserId, code, model, imageLimit);
    }

    // ─────────────────────────────────────────────────────────────
    // 멀티모달 LLM Enrich 배치 (PDF v1.0 Stage 2 통합 호출 — Sonnet/Haiku 선택)
    // 기존 scrape용 batch-fetch와 별도 진행 상태로 관리됨.
    // ─────────────────────────────────────────────────────────────
    @PostMapping("/batch-enrich")
    public Map<String, Object> batchEnrich(@PathVariable long advertiserId,
                                             @RequestParam(defaultValue = "50") int count,
                                             @RequestParam(defaultValue = "haiku") String model,
                                             @RequestParam(defaultValue = "30") int imageLimit,
                                             @RequestParam(defaultValue = "2") int concurrency,
                                             @RequestParam(defaultValue = "0") long intervalMs,
                                             @RequestParam(defaultValue = "bge") String embedBackend) {
        ProductEnrichService.BatchProgress p = service.getEnrichProgress(advertiserId);
        if ("RUNNING".equals(p.status)) {
            return Map.of("status", "already_running", "advertiserId", advertiserId,
                    "done", p.done, "total", p.total);
        }
        Thread t = new Thread(() -> {
            try {
                service.batchEnrich(advertiserId, count, model, imageLimit, concurrency, intervalMs, embedBackend);
            } catch (Throwable th) {
                org.slf4j.LoggerFactory.getLogger(ProductEnrichController.class)
                        .error("[BatchEnrich] thread crashed: {}", th.getMessage(), th);
                p.status = "FAILED";
                p.lastError = th.getMessage();
            }
        }, "batch-enrich-" + advertiserId);
        t.setDaemon(true);
        t.start();
        return Map.of("status", "started", "advertiserId", advertiserId,
                "count", count, "model", model, "imageLimit", imageLimit,
                "concurrency", concurrency, "intervalMs", intervalMs, "embedBackend", embedBackend);
    }

    @GetMapping("/batch-enrich/status")
    public Map<String, Object> batchEnrichStatus(@PathVariable long advertiserId) {
        ProductEnrichService.BatchProgress p = service.getEnrichProgress(advertiserId);
        return Map.of(
                "status", p.status,
                "total", p.total,
                "done", p.done,
                "ok", p.ok,
                "fail", p.fail,
                "currentCode", p.currentCode == null ? "" : p.currentCode,
                "elapsedMs", p.elapsedMs,
                "lastError", p.lastError == null ? "" : p.lastError
        );
    }

    @PostMapping("/batch-enrich/cancel")
    public Map<String, Object> batchEnrichCancel(@PathVariable long advertiserId) {
        service.cancelEnrich(advertiserId);
        return Map.of("status", "cancel_requested", "advertiserId", advertiserId);
    }

}
