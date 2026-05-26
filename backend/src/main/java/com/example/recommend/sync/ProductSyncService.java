package com.example.recommend.sync;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.math.BigDecimal;

/**
 * 메이크샵 동기화: 페이지 단위로 API 호출 → rv_products UPSERT.
 * 진행상태는 product_sync_status에 페이지마다 저장 (재개 가능).
 */
@Service
public class ProductSyncService {

    private static final Logger log = LoggerFactory.getLogger(ProductSyncService.class);
    private static final int PAGE_SIZE = 500;
    private static final int MAX_PAGES_PER_RUN = 200; // 안전핀 — 10만 상품
    private static final ObjectMapper M = new ObjectMapper();

    private final JdbcTemplate jdbc;
    private final AdvertiserRepository advertisers;
    private final MakeshopClient makeshop;

    public ProductSyncService(JdbcTemplate jdbc, AdvertiserRepository advertisers, MakeshopClient makeshop) {
        this.jdbc = jdbc;
        this.advertisers = advertisers;
        this.makeshop = makeshop;
    }

    public record SyncResult(long advertiserId, int totalCount, int savedCount, int pagesProcessed, String status, String error) {}

    public SyncResult syncMakeshop(long advertiserId) {
        Advertiser a = advertisers.findById(advertiserId)
                .orElseThrow(() -> new IllegalArgumentException("advertiser not found: " + advertiserId));
        if (!"makeshop".equals(a.hostType())) {
            throw new IllegalArgumentException("host_type is not makeshop: " + a.hostType());
        }
        if (a.shopUrl() == null || a.shopKey() == null || a.licenseKey() == null) {
            throw new IllegalArgumentException("shop_url / shop_key / license_key required");
        }

        int total;
        try {
            total = makeshop.fetchTotalCount(a.shopUrl(), a.shopKey(), a.licenseKey());
        } catch (IOException e) {
            return failStatus(advertiserId, 0, 0, "fetchTotalCount failed: " + e.getMessage());
        }
        log.info("[Sync] advertiser={} total={}", advertiserId, total);

        upsertStatus(advertiserId, "IN_PROGRESS", 0, total, 0, null);

        int totalPages = Math.max(1, (total + PAGE_SIZE - 1) / PAGE_SIZE);
        int saved = 0;
        int page = 1;
        for (; page <= totalPages && page <= MAX_PAGES_PER_RUN; page++) {
            try {
                MakeshopClient.ProductListResponse resp =
                        makeshop.fetchProducts(a.shopUrl(), a.shopKey(), a.licenseKey(), page, PAGE_SIZE);
                if (resp.list() == null || !resp.list().isArray() || resp.list().size() == 0) {
                    log.info("[Sync] advertiser={} page={} empty, stop", advertiserId, page);
                    break;
                }
                int pageSaved = upsertProducts(advertiserId, a.shopUrl(), resp.list());
                saved += pageSaved;
                log.info("[Sync] advertiser={} page={}/{} saved={} (total saved={})",
                        advertiserId, page, totalPages, pageSaved, saved);
                upsertStatus(advertiserId, "IN_PROGRESS", page, total, saved, null);
            } catch (Exception e) {
                String msg = "page " + page + ": " + e.getMessage();
                log.error("[Sync] {}", msg, e);
                upsertStatus(advertiserId, "FAILED", page, total, saved, msg);
                return new SyncResult(advertiserId, total, saved, page - 1, "FAILED", msg);
            }
        }

        upsertStatus(advertiserId, "COMPLETED", page - 1, total, saved, null);
        return new SyncResult(advertiserId, total, saved, page - 1, "COMPLETED", null);
    }

    private int upsertProducts(long advertiserId, String shopUrl, JsonNode list) {
        String clean = shopUrl.endsWith("/") ? shopUrl.substring(0, shopUrl.length() - 1) : shopUrl;
        int saved = 0;
        for (JsonNode item : list) {
            String code = textOrNull(item, "uid");
            if (code == null) continue;
            String name = textOrNull(item, "product_name");
            if (name == null) name = "(no name)";
            String brandcode = textOrNull(item, "brandcode");
            String cate1 = textOrNull(item, "cate1");
            BigDecimal sellPrice = parseBigDecimal(item, "sellprice");
            BigDecimal consumerPrice = parseBigDecimal(item, "consumerprice");
            String maxImage = textOrNull(item, "maximage");
            if (maxImage != null && maxImage.startsWith("/")) maxImage = clean + maxImage;
            String productUrl = clean + "/shop/shopdetail.html?branduid=" + code;
            String rawJson;
            try {
                rawJson = M.writeValueAsString(item);
            } catch (Exception e) {
                rawJson = "{}";
            }

            int rows = jdbc.update(
                    "INSERT INTO rv_products " +
                            "(advertiser_id, product_code, product_origin_code, product_name, company_nm, price, sale_price, product_url, image_url, raw_payload, synced_at, mod_date) " +
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?::jsonb, NOW(), NOW()) " +
                            "ON CONFLICT (advertiser_id, product_code) DO UPDATE SET " +
                            "  product_origin_code = EXCLUDED.product_origin_code, " +
                            "  product_name        = EXCLUDED.product_name, " +
                            "  company_nm          = EXCLUDED.company_nm, " +
                            "  price               = EXCLUDED.price, " +
                            "  sale_price          = EXCLUDED.sale_price, " +
                            "  product_url         = EXCLUDED.product_url, " +
                            "  image_url           = EXCLUDED.image_url, " +
                            "  raw_payload         = EXCLUDED.raw_payload, " +
                            "  synced_at           = NOW(), " +
                            "  mod_date            = NOW()",
                    advertiserId, code, brandcode, name, cate1,
                    consumerPrice, sellPrice, productUrl, maxImage, rawJson
            );
            if (rows > 0) saved++;
        }
        return saved;
    }

    private void upsertStatus(long advertiserId, String status, int lastPage, int total, int saved, String error) {
        jdbc.update(
                "INSERT INTO product_sync_status (advertiser_id, sync_type, status, last_page, total_count, saved_count, error_message, started_at, updated_at) " +
                        "VALUES (?, 'PRODUCT_LIST', ?, ?, ?, ?, ?, NOW(), NOW()) " +
                        "ON CONFLICT (advertiser_id, sync_type) DO UPDATE SET " +
                        "  status        = EXCLUDED.status, " +
                        "  last_page     = EXCLUDED.last_page, " +
                        "  total_count   = EXCLUDED.total_count, " +
                        "  saved_count   = EXCLUDED.saved_count, " +
                        "  error_message = EXCLUDED.error_message, " +
                        "  updated_at    = NOW()",
                advertiserId, status, lastPage, total, saved, error
        );
    }

    private SyncResult failStatus(long advertiserId, int total, int saved, String msg) {
        upsertStatus(advertiserId, "FAILED", 0, total, saved, msg);
        return new SyncResult(advertiserId, total, saved, 0, "FAILED", msg);
    }

    private static String textOrNull(JsonNode n, String f) {
        JsonNode v = n.get(f);
        if (v == null || v.isNull()) return null;
        String s = v.asText("");
        return s.isEmpty() ? null : s;
    }

    private static BigDecimal parseBigDecimal(JsonNode n, String f) {
        String s = textOrNull(n, f);
        if (s == null) return null;
        try {
            return new BigDecimal(s.replaceAll(",", ""));
        } catch (NumberFormatException e) {
            return null;
        }
    }
}
