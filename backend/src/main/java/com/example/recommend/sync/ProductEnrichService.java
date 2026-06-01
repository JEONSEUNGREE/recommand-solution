package com.example.recommend.sync;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.time.Duration;
import java.util.Objects;

/** 한 상품 단위 enrich — embedder의 /scrape/fetch, /scrape/ocr, /scrape/vlm 호출 + rv_products UPDATE. */
@Service
public class ProductEnrichService {

    private static final Logger log = LoggerFactory.getLogger(ProductEnrichService.class);
    private static final MediaType JSON = MediaType.parse("application/json");

    private final OkHttpClient http;
    private final ObjectMapper mapper;
    private final AdvertiserRepository advertisers;
    private final AdvertiserSelectorRepository selectors;
    private final ImageBlockRepository imageBlocks;
    private final RvProductRepository products;
    private final String embedderBase;
    private final String apiToken;

    public ProductEnrichService(AdvertiserRepository advertisers,
                                AdvertiserSelectorRepository selectors,
                                ImageBlockRepository imageBlocks,
                                RvProductRepository products,
                                MakeshopClient makeshop,
                                ObjectMapper mapper,
                                org.springframework.jdbc.core.JdbcTemplate jdbc,
                                com.example.recommend.config.AppProperties props) {
        this.advertisers = advertisers;
        this.selectors = selectors;
        this.imageBlocks = imageBlocks;
        this.products = products;
        this.makeshop = makeshop;
        this.mapper = mapper;
        this.jdbc = jdbc;
        this.embedderBase = props.embedder().baseUrl();
        this.apiToken = props.embedder().apiToken();
        Duration t = Duration.ofMinutes(10); // VLM/OCR 충분 여유
        this.http = new OkHttpClient.Builder()
                .connectTimeout(t).readTimeout(t).writeTimeout(t).build();
    }

    public JsonNode scrape(long advertiserId, String productCode) throws IOException {
        RvProduct p = products.findOne(advertiserId, productCode)
                .orElseThrow(() -> new IllegalArgumentException("product not found: " + productCode));
        if (p.productUrl() == null || p.productUrl().isBlank()) {
            throw new IllegalStateException("product_url is empty for " + productCode);
        }
        Advertiser a = advertisers.findById(advertiserId).orElseThrow();

        // 새 1:N 테이블에서 selector 가져오기. priority 순.
        java.util.List<String> nameSels = selectors.findEnabled(advertiserId, "name").stream().map(AdvertiserSelector::selector).toList();
        java.util.List<String> detailSels = selectors.findEnabled(advertiserId, "detail").stream().map(AdvertiserSelector::selector).toList();
        java.util.List<String> priceSels = selectors.findEnabled(advertiserId, "price").stream().map(AdvertiserSelector::selector).toList();
        log.info("[Enrich] scrape advertiser={} code={} url={} name_sels={} detail_sels={}",
                advertiserId, productCode, p.productUrl(), nameSels, detailSels);

        java.util.Map<String, Object> payload = new java.util.LinkedHashMap<>();
        payload.put("advertiser_id", advertiserId);
        payload.put("product_code", productCode);
        payload.put("url", p.productUrl());
        payload.put("max_images", 30);
        if (!detailSels.isEmpty()) payload.put("selector_detail", detailSels);
        if (!nameSels.isEmpty()) payload.put("selector_name", nameSels);
        if (!priceSels.isEmpty()) payload.put("selector_price", priceSels);
        if (a.imageAttrs() != null && !a.imageAttrs().isBlank()) payload.put("image_attrs", a.imageAttrs());
        if (a.detailAnchorStart() != null && !a.detailAnchorStart().isBlank()) payload.put("detail_anchor_start", a.detailAnchorStart());
        if (a.detailAnchorEnd() != null && !a.detailAnchorEnd().isBlank()) payload.put("detail_anchor_end", a.detailAnchorEnd());

        java.util.List<String> blocks = imageBlocks.findEnabledPatterns(advertiserId);
        if (!blocks.isEmpty()) payload.put("image_url_blocks", blocks);
        String body = mapper.writeValueAsString(payload);
        try {
            JsonNode res = call("/scrape/fetch", body);
            String bodyText = res.path("body_text").asText("");
            int bodyTextLen = res.path("body_text_len").asInt(bodyText.length());
            String htmlPath = res.path("html_path").asText(null);
            String imageDir = res.path("image_dir").asText(null);
            int imgCount = res.path("image_count").asInt(0);
            products.updateAfterScrape(advertiserId, productCode, bodyText, bodyTextLen, htmlPath, imageDir, imgCount, "scraped", null);
            return res;
        } catch (Exception e) {
            log.error("[Enrich] scrape failed code={} err={}", productCode, e.getMessage());
            products.updateAfterScrape(advertiserId, productCode, null, 0, null, null, 0, "failed", e.getMessage());
            throw e;
        }
    }

    private final org.springframework.jdbc.core.JdbcTemplate jdbc;

    /** 메모리에 보관되는 배치 진행상황. JVM 재시작 시 사라짐. */
    public static final class BatchProgress {
        public volatile int total;
        public volatile int done;
        public volatile int ok;
        public volatile int fail;
        public volatile String status = "IDLE";  // IDLE | RUNNING | COMPLETED | FAILED | CANCELLED
        public volatile String currentCode;
        public volatile long startedAtMs;
        public volatile long elapsedMs;
        public volatile String lastError;
        public final java.util.concurrent.atomic.AtomicBoolean cancelFlag = new java.util.concurrent.atomic.AtomicBoolean(false);
    }

    private final java.util.concurrent.ConcurrentHashMap<Long, BatchProgress> batchProgress = new java.util.concurrent.ConcurrentHashMap<>();
    // LLM enrich 진행상황 — fetch와 별도 슬롯 (광고주별 동시 진행 가능하게)
    private final java.util.concurrent.ConcurrentHashMap<Long, BatchProgress> enrichProgress = new java.util.concurrent.ConcurrentHashMap<>();

    private final MakeshopClient makeshop;

    public BatchProgress getBatchProgress(long advertiserId) {
        return batchProgress.computeIfAbsent(advertiserId, k -> new BatchProgress());
    }

    public BatchProgress getEnrichProgress(long advertiserId) {
        return enrichProgress.computeIfAbsent(advertiserId, k -> new BatchProgress());
    }

    public void cancelBatch(long advertiserId) {
        BatchProgress p = batchProgress.get(advertiserId);
        if (p != null) p.cancelFlag.set(true);
    }

    public void cancelEnrich(long advertiserId) {
        BatchProgress p = enrichProgress.get(advertiserId);
        if (p != null) p.cancelFlag.set(true);
    }

    /** 메이크샵 OpenAPI의 product_content를 받아 embedder /scrape/from-html에 위임. 페이지 다운로드 X. */
    public java.util.Map<String, Object> batchFetchApi(long advertiserId, int count, long intervalMs, int concurrency, int batchSize) {
        if (concurrency < 1) concurrency = 1;
        if (concurrency > 32) concurrency = 32;
        if (batchSize < 1) batchSize = 100;
        if (batchSize > 200) batchSize = 200;

        Advertiser a = advertisers.findById(advertiserId).orElseThrow();
        if (!"makeshop".equals(a.hostType())) {
            throw new IllegalStateException("batchFetchApi: host_type must be makeshop");
        }
        java.util.List<String> uids = jdbc.queryForList(
                "SELECT product_code FROM rv_products" +
                " WHERE advertiser_id=? AND scrape_status IS NULL" +
                "   AND (raw_payload IS NULL OR raw_payload->>'display' = 'Y')" +
                "   AND product_name NOT LIKE '%고객님%'" +
                "   AND product_name NOT LIKE '%결재창%'" +
                "   AND product_name NOT LIKE '%결제창%'" +
                " ORDER BY id ASC LIMIT ?",
                String.class, advertiserId, count
        );
        java.util.List<String> nameSels = selectors.findEnabled(advertiserId, "name").stream().map(AdvertiserSelector::selector).toList();
        java.util.List<String> detailSels = selectors.findEnabled(advertiserId, "detail").stream().map(AdvertiserSelector::selector).toList();
        java.util.List<String> blocks = imageBlocks.findEnabledPatterns(advertiserId);

        log.info("[BatchFetchApi] advertiser={} picked={} batch={} concurrency={} interval={}ms",
                advertiserId, uids.size(), batchSize, concurrency, intervalMs);

        BatchProgress p = getBatchProgress(advertiserId);
        p.total = uids.size();
        p.done = 0; p.ok = 0; p.fail = 0;
        p.status = "RUNNING";
        p.startedAtMs = System.currentTimeMillis();
        p.lastError = null;
        p.cancelFlag.set(false);
        long t0 = p.startedAtMs;

        java.util.concurrent.atomic.AtomicInteger doneCnt = new java.util.concurrent.atomic.AtomicInteger();
        java.util.concurrent.atomic.AtomicInteger okCnt = new java.util.concurrent.atomic.AtomicInteger();
        java.util.concurrent.atomic.AtomicInteger failCnt = new java.util.concurrent.atomic.AtomicInteger();

        java.util.concurrent.ExecutorService imgPool = java.util.concurrent.Executors.newFixedThreadPool(concurrency);

        // batchSize씩 끊어서 메이크샵 API 호출
        for (int start = 0; start < uids.size(); start += batchSize) {
            if (p.cancelFlag.get()) break;
            java.util.List<String> chunk = uids.subList(start, Math.min(start + batchSize, uids.size()));
            JsonNode resp;
            try {
                resp = makeshop.fetchProductContents(a.shopUrl(), a.shopKey(), a.licenseKey(), chunk, "product_content,uid");
            } catch (IOException ie) {
                log.warn("[BatchFetchApi] api call failed: {}", ie.getMessage());
                p.lastError = "api: " + ie.getMessage();
                for (String uid : chunk) {
                    failCnt.incrementAndGet();
                    int d = doneCnt.incrementAndGet();
                    p.done = d; p.fail = failCnt.get();
                }
                continue;
            }
            JsonNode list = resp.path("list");
            if (!list.isArray()) {
                log.warn("[BatchFetchApi] unexpected resp: {}", resp.toString().substring(0, Math.min(200, resp.toString().length())));
                continue;
            }

            for (JsonNode item : list) {
                if (p.cancelFlag.get()) break;
                final String uid = item.path("uid").asText("");
                final String b64 = item.path("product_content").asText("");
                imgPool.submit(() -> {
                    if (p.cancelFlag.get()) return;
                    try {
                        p.currentCode = uid;
                        String html;
                        if (b64.isBlank()) {
                            html = "<html></html>";
                        } else {
                            html = new String(java.util.Base64.getDecoder().decode(b64), java.nio.charset.StandardCharsets.UTF_8);
                        }
                        String productUrl = a.shopUrl().replaceAll("/$", "") + "/shop/shopdetail.html?branduid=" + uid;

                        java.util.Map<String, Object> payload = new java.util.LinkedHashMap<>();
                        payload.put("advertiser_id", advertiserId);
                        payload.put("product_code", uid);
                        payload.put("url", productUrl);
                        payload.put("html", html);
                        payload.put("max_images", 30);
                        if (!detailSels.isEmpty()) payload.put("selector_detail", detailSels);
                        if (!nameSels.isEmpty()) payload.put("selector_name", nameSels);
                        if (a.imageAttrs() != null && !a.imageAttrs().isBlank()) payload.put("image_attrs", a.imageAttrs());
                        if (a.detailAnchorStart() != null && !a.detailAnchorStart().isBlank()) payload.put("detail_anchor_start", a.detailAnchorStart());
                        if (a.detailAnchorEnd() != null && !a.detailAnchorEnd().isBlank()) payload.put("detail_anchor_end", a.detailAnchorEnd());
                        if (!blocks.isEmpty()) payload.put("image_url_blocks", blocks);

                        String body = mapper.writeValueAsString(payload);
                        Request.Builder rb = new Request.Builder()
                                .url(embedderBase + "/scrape/from-html")
                                .post(okhttp3.RequestBody.create(body, okhttp3.MediaType.parse("application/json")));
                        if (apiToken != null && !apiToken.isBlank()) {
                            rb.header("Authorization", "Bearer " + apiToken);
                        }
                        Request req = rb.build();
                        try (Response res = http.newCall(req).execute()) {
                            String text = res.body() != null ? res.body().string() : "";
                            if (!res.isSuccessful()) {
                                throw new IOException("from-html HTTP " + res.code() + ": " + text.substring(0, Math.min(200, text.length())));
                            }
                            JsonNode r = mapper.readTree(text);
                            String bodyText = r.path("body_text").asText("");
                            int bodyTextLen = r.path("body_text_len").asInt(bodyText.length());
                            String htmlPath = r.path("html_path").asText(null);
                            String imageDir = r.path("image_dir").asText(null);
                            int imgCount = r.path("image_count").asInt(0);
                            products.updateAfterScrape(advertiserId, uid, bodyText, bodyTextLen, htmlPath, imageDir, imgCount, "scraped", null);
                            okCnt.incrementAndGet();
                        }
                    } catch (Exception e) {
                        failCnt.incrementAndGet();
                        p.lastError = uid + ": " + e.getMessage();
                        try { products.updateAfterScrape(advertiserId, uid, null, 0, null, null, 0, "failed", e.getMessage()); } catch (Exception ignored) {}
                    } finally {
                        int d = doneCnt.incrementAndGet();
                        p.done = d; p.ok = okCnt.get(); p.fail = failCnt.get();
                        p.elapsedMs = System.currentTimeMillis() - t0;
                        if (d % 10 == 0 || d == uids.size()) {
                            log.info("[BatchFetchApi] {}/{} done ok={} fail={} elapsed={}s",
                                    d, uids.size(), p.ok, p.fail, p.elapsedMs / 1000);
                        }
                    }
                });
            }
            // batch 사이 sleep — 메이크샵 API 부하 분산
            if (intervalMs > 0 && !p.cancelFlag.get()) {
                try { Thread.sleep(intervalMs); } catch (InterruptedException ie) { Thread.currentThread().interrupt(); }
            }
        }

        imgPool.shutdown();
        try {
            while (!imgPool.awaitTermination(2, java.util.concurrent.TimeUnit.SECONDS)) {
                if (p.cancelFlag.get()) { imgPool.shutdownNow(); break; }
                if (System.currentTimeMillis() - t0 > 4 * 3600_000L) { imgPool.shutdownNow(); break; }
            }
        } catch (InterruptedException ie) {
            Thread.currentThread().interrupt();
            imgPool.shutdownNow();
        }
        p.status = p.cancelFlag.get() ? "CANCELLED" : "COMPLETED";
        p.elapsedMs = System.currentTimeMillis() - t0;
        log.info("[BatchFetchApi] {} total={} ok={} fail={} elapsed={}s",
                p.status, uids.size(), p.ok, p.fail, p.elapsedMs / 1000);
        return java.util.Map.of("advertiserId", advertiserId, "total", uids.size(),
                "ok", p.ok, "fail", p.fail, "elapsedSec", p.elapsedMs / 1000, "status", p.status);
    }

    /** 미스크랩(NULL) 상태 상품을 N개 워커 병렬로 fetch. 각 워커는 한 상품 처리 후 intervalMs 만큼 sleep. */
    public java.util.Map<String, Object> batchFetch(long advertiserId, int count, long intervalMs, int concurrency) {
        if (concurrency < 1) concurrency = 1;
        if (concurrency > 32) concurrency = 32;
        java.util.List<String> codes = jdbc.queryForList(
                "SELECT product_code FROM rv_products" +
                " WHERE advertiser_id=? AND scrape_status IS NULL" +
                "   AND (raw_payload IS NULL OR raw_payload->>'display' = 'Y')" +
                "   AND product_name NOT LIKE '%고객님%'" +
                "   AND product_name NOT LIKE '%결재창%'" +
                "   AND product_name NOT LIKE '%결제창%'" +
                " ORDER BY id ASC LIMIT ?",
                String.class, advertiserId, count
        );
        log.info("[BatchFetch] advertiser={} picked={} concurrency={} interval={}ms",
                advertiserId, codes.size(), concurrency, intervalMs);

        BatchProgress p = getBatchProgress(advertiserId);
        p.total = codes.size();
        p.done = 0; p.ok = 0; p.fail = 0;
        p.status = "RUNNING";
        p.startedAtMs = System.currentTimeMillis();
        p.lastError = null;
        p.cancelFlag.set(false);
        long t0 = p.startedAtMs;

        java.util.concurrent.atomic.AtomicInteger doneCnt = new java.util.concurrent.atomic.AtomicInteger();
        java.util.concurrent.atomic.AtomicInteger okCnt = new java.util.concurrent.atomic.AtomicInteger();
        java.util.concurrent.atomic.AtomicInteger failCnt = new java.util.concurrent.atomic.AtomicInteger();

        java.util.concurrent.ExecutorService pool = java.util.concurrent.Executors.newFixedThreadPool(concurrency);
        final long intervalFinal = intervalMs;
        for (String code : codes) {
            pool.submit(() -> {
                if (p.cancelFlag.get()) return;
                try {
                    p.currentCode = code;
                    scrape(advertiserId, code);
                    p.ok = okCnt.incrementAndGet();
                } catch (Exception e) {
                    p.fail = failCnt.incrementAndGet();
                    p.lastError = code + ": " + e.getMessage();
                    log.warn("[BatchFetch] code={} FAIL {}", code, e.getMessage());
                } finally {
                    int d = doneCnt.incrementAndGet();
                    p.done = d;
                    p.elapsedMs = System.currentTimeMillis() - t0;
                    if (d % 5 == 0 || d == codes.size()) {
                        log.info("[BatchFetch] {}/{} done ok={} fail={} elapsed={}s",
                                d, codes.size(), p.ok, p.fail, p.elapsedMs / 1000);
                    }
                    if (intervalFinal > 0 && !p.cancelFlag.get()) {
                        try { Thread.sleep(intervalFinal); } catch (InterruptedException ie) { Thread.currentThread().interrupt(); }
                    }
                }
            });
        }
        pool.shutdown();
        try {
            // 안전망: 최대 4시간 대기. cancel 들어오면 shutdownNow.
            while (!pool.awaitTermination(2, java.util.concurrent.TimeUnit.SECONDS)) {
                if (p.cancelFlag.get()) {
                    pool.shutdownNow();
                    break;
                }
                if (System.currentTimeMillis() - t0 > 4 * 3600_000L) {
                    pool.shutdownNow();
                    break;
                }
            }
        } catch (InterruptedException ie) {
            Thread.currentThread().interrupt();
            pool.shutdownNow();
        }

        if (p.cancelFlag.get()) p.status = "CANCELLED";
        else p.status = "COMPLETED";
        p.elapsedMs = System.currentTimeMillis() - t0;
        log.info("[BatchFetch] {} advertiser={} total={} ok={} fail={} elapsed={}s",
                p.status, advertiserId, codes.size(), p.ok, p.fail, p.elapsedMs / 1000);
        return java.util.Map.of("advertiserId", advertiserId, "total", codes.size(),
                "ok", p.ok, "fail", p.fail, "elapsedSec", p.elapsedMs / 1000, "status", p.status);
    }

    public JsonNode enrichOcr(long advertiserId, String productCode) throws IOException {
        return enrich(advertiserId, productCode, "/scrape/ocr", "ocr", 8);
    }

    public JsonNode enrichVlm(long advertiserId, String productCode) throws IOException {
        return enrich(advertiserId, productCode, "/scrape/vlm", "vlm", 4);
    }

    /** 단일 상품 멀티모달 LLM enrich (즉시 동기 호출). 이미지 N장으로 1번 호출. */
    public JsonNode enrichLlm(long advertiserId, String productCode, String model, int imageLimit) throws IOException {
        return enrichLlm(advertiserId, productCode, model, imageLimit, "cli");
    }

    /** backend 지정 버전 — "cli" (claude.exe) 또는 "api" (ANTHROPIC_API_KEY). */
    public JsonNode enrichLlm(long advertiserId, String productCode, String model, int imageLimit, String backend) throws IOException {
        Long rvId = jdbc.queryForObject(
                "SELECT id FROM rv_products WHERE advertiser_id=? AND product_code=?",
                Long.class, advertiserId, productCode
        );
        if (rvId == null) throw new IllegalArgumentException("rv_products not found: " + productCode);
        java.util.Map<String, Object> payload = new java.util.LinkedHashMap<>();
        payload.put("rv_product_id", rvId);
        payload.put("model", model == null || model.isBlank() ? "haiku" : model);
        payload.put("image_limit", imageLimit < 1 ? 10 : Math.min(imageLimit, 100));
        payload.put("save_db", true);
        payload.put("backend", backend == null || backend.isBlank() ? "cli" : backend);
        return call("/enrich-llm/run", mapper.writeValueAsString(payload));
    }

    private JsonNode enrich(long advertiserId, String productCode, String path, String method, int maxImages) throws IOException {
        // 제외 마킹된 파일명 list 가져오기
        java.util.List<String> excluded = jdbc.queryForList(
                "SELECT image_filename FROM rv_product_excluded_images WHERE advertiser_id=? AND product_code=?",
                String.class, advertiserId, productCode
        );
        log.info("[Enrich] {} advertiser={} code={} excluded={}", method, advertiserId, productCode, excluded.size());
        java.util.Map<String, Object> payload = new java.util.LinkedHashMap<>();
        payload.put("advertiser_id", advertiserId);
        payload.put("product_code", productCode);
        payload.put("max_images", maxImages);
        if (!excluded.isEmpty()) payload.put("exclude_filenames", excluded);
        String body = mapper.writeValueAsString(payload);
        try {
            JsonNode res = call(path, body);
            String enriched = res.path("enriched_info").asText("");
            products.updateAfterEnrich(advertiserId, productCode, method, enriched, null);
            return res;
        } catch (Exception e) {
            log.error("[Enrich] {} failed code={} err={}", method, productCode, e.getMessage());
            products.updateAfterEnrich(advertiserId, productCode, method, null, e.getMessage());
            throw e;
        }
    }

    private JsonNode call(String path, String jsonBody) throws IOException {
        Request.Builder rb = new Request.Builder()
                .url(embedderBase + path)
                .post(RequestBody.create(jsonBody, JSON));
        if (apiToken != null && !apiToken.isBlank()) {
            rb.header("Authorization", "Bearer " + apiToken);
        }
        Request req = rb.build();
        try (Response res = http.newCall(req).execute()) {
            String text = Objects.requireNonNull(res.body()).string();
            if (!res.isSuccessful()) {
                throw new IOException("embedder " + path + " HTTP " + res.code() + ": " + text.substring(0, Math.min(300, text.length())));
            }
            return mapper.readTree(text);
        }
    }

    /**
     * LLM 기반 멀티모달 enrich 배치.
     * scrape_status='scraped' 이고 product_enriched에 아직 없는 상품을 N개 골라
     * embedder /enrich-llm/run을 워커풀로 호출.
     */
    public java.util.Map<String, Object> batchEnrich(long advertiserId,
                                                       int count,
                                                       String model,
                                                       int imageLimit,
                                                       int concurrency,
                                                       long intervalMs,
                                                       String embedBackend) {
        return batchEnrich(advertiserId, count, model, imageLimit, concurrency, intervalMs, embedBackend, "cli");
    }

    /** backend 지정 버전 — "cli" (claude.exe) 또는 "api" (ANTHROPIC_API_KEY). */
    public java.util.Map<String, Object> batchEnrich(long advertiserId,
                                                       int count,
                                                       String model,
                                                       int imageLimit,
                                                       int concurrency,
                                                       long intervalMs,
                                                       String embedBackend,
                                                       String backend) {
        if (concurrency < 1) concurrency = 1;
        if (concurrency > 8) concurrency = 8;
        if (imageLimit < 1) imageLimit = 1;
        if (imageLimit > 100) imageLimit = 100;
        if (model == null || model.isBlank()) model = "haiku";
        if (embedBackend == null || embedBackend.isBlank()) embedBackend = "bge";
        if (backend == null || backend.isBlank()) backend = "cli";

        // 벡터화까지 완료 안 된 상품을 모두 — product_enriched에 없거나 enrich_status != 'embedded'
        java.util.List<Long> ids = jdbc.queryForList(
                "SELECT rv.id FROM rv_products rv " +
                " LEFT JOIN product_enriched pe ON pe.rv_product_id = rv.id " +
                " WHERE rv.advertiser_id=? AND rv.scrape_status='scraped' " +
                "   AND (pe.rv_product_id IS NULL OR pe.enrich_status <> 'embedded') " +
                " ORDER BY rv.id ASC LIMIT ?",
                Long.class, advertiserId, count
        );
        log.info("[BatchEnrich] advertiser={} picked={} model={} imageLimit={} concurrency={} interval={}ms",
                advertiserId, ids.size(), model, imageLimit, concurrency, intervalMs);

        BatchProgress p = getEnrichProgress(advertiserId);
        p.total = ids.size();
        p.done = 0; p.ok = 0; p.fail = 0;
        p.status = "RUNNING";
        p.startedAtMs = System.currentTimeMillis();
        p.lastError = null;
        p.cancelFlag.set(false);
        long t0 = p.startedAtMs;

        java.util.concurrent.atomic.AtomicInteger doneCnt = new java.util.concurrent.atomic.AtomicInteger();
        java.util.concurrent.atomic.AtomicInteger okCnt = new java.util.concurrent.atomic.AtomicInteger();
        java.util.concurrent.atomic.AtomicInteger failCnt = new java.util.concurrent.atomic.AtomicInteger();
        java.util.concurrent.ExecutorService pool = java.util.concurrent.Executors.newFixedThreadPool(concurrency);
        final String fModel = model;
        final int fImageLimit = imageLimit;
        final long fIntervalMs = intervalMs;
        final String fEmbedBackend = embedBackend;
        final String fBackend = backend;

        for (Long rvId : ids) {
            if (p.cancelFlag.get()) break;
            pool.submit(() -> {
                if (p.cancelFlag.get()) return;
                try {
                    p.currentCode = String.valueOf(rvId);
                    java.util.Map<String, Object> payload = new java.util.LinkedHashMap<>();
                    payload.put("rv_product_id", rvId);
                    payload.put("model", fModel);
                    payload.put("image_limit", fImageLimit);
                    payload.put("save_db", true);
                    payload.put("also_embed", true);
                    payload.put("embed_backend", fEmbedBackend);
                    payload.put("skip_enrich_if_done", true);
                    payload.put("backend", fBackend);
                    String body = mapper.writeValueAsString(payload);
                    Request.Builder rb = new Request.Builder()
                            .url(embedderBase + "/enrich-llm/run")
                            .post(okhttp3.RequestBody.create(body, okhttp3.MediaType.parse("application/json")));
                    if (apiToken != null && !apiToken.isBlank()) {
                        rb.header("Authorization", "Bearer " + apiToken);
                    }
                    Request req = rb.build();
                    try (Response res = http.newCall(req).execute()) {
                        String text = res.body() != null ? res.body().string() : "";
                        if (!res.isSuccessful()) {
                            throw new IOException("enrich-llm HTTP " + res.code() + ": " + text.substring(0, Math.min(300, text.length())));
                        }
                        okCnt.incrementAndGet();
                    }
                } catch (Exception e) {
                    failCnt.incrementAndGet();
                    p.lastError = "rv_id=" + rvId + ": " + e.getMessage();
                    log.warn("[BatchEnrich] FAIL rv_id={}: {}", rvId, e.getMessage());
                } finally {
                    int d = doneCnt.incrementAndGet();
                    p.done = d; p.ok = okCnt.get(); p.fail = failCnt.get();
                    p.elapsedMs = System.currentTimeMillis() - t0;
                    if (d % 5 == 0 || d == ids.size()) {
                        log.info("[BatchEnrich] {}/{} done ok={} fail={} elapsed={}s",
                                d, ids.size(), p.ok, p.fail, p.elapsedMs / 1000);
                    }
                }
                // 워커 단위 sleep — 모델 부하 분산
                if (fIntervalMs > 0 && !p.cancelFlag.get()) {
                    try { Thread.sleep(fIntervalMs); } catch (InterruptedException ie) { Thread.currentThread().interrupt(); }
                }
            });
        }
        pool.shutdown();
        try {
            pool.awaitTermination(2, java.util.concurrent.TimeUnit.HOURS);
        } catch (InterruptedException ie) {
            Thread.currentThread().interrupt();
        }
        p.elapsedMs = System.currentTimeMillis() - t0;
        p.status = p.cancelFlag.get() ? "CANCELLED" : "COMPLETED";
        log.info("[BatchEnrich] FINISHED advertiser={} total={} ok={} fail={} elapsed={}s status={}",
                advertiserId, p.total, p.ok, p.fail, p.elapsedMs / 1000, p.status);
        return java.util.Map.of("advertiserId", advertiserId, "total", p.total,
                "ok", p.ok, "fail", p.fail, "elapsedSec", p.elapsedMs / 1000, "status", p.status);
    }
}
