package com.example.recommend.sync;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.HttpUrl;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.time.Duration;

/**
 * MakeShop Open API 클라이언트.
 * 참조: ireview-admin/external/mall/makeshop/MakeshopApiService.java
 *
 * Endpoint: {shopUrl}/api/list/open_api.html (GET)
 *   headers: Shopkey, Licensekey
 *   params:  mode=search & type=product & limit & page
 */
@Component
public class MakeshopClient {

    private static final Logger log = LoggerFactory.getLogger(MakeshopClient.class);
    private static final String UA =
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";

    private final OkHttpClient http;
    private final ObjectMapper mapper = new ObjectMapper();

    public MakeshopClient() {
        Duration t = Duration.ofMinutes(2);
        this.http = new OkHttpClient.Builder()
                .connectTimeout(t).readTimeout(t).writeTimeout(t).build();
    }

    public record ProductListResponse(int total, int totalPage, int count, JsonNode list) {}

    /** 상품 목록 조회. page는 1-base. limit MAX 5000 (메이크샵 API 문서 기준). */
    public ProductListResponse fetchProducts(String shopUrl, String shopKey, String licenseKey,
                                              int page, int limit) throws IOException {
        String cleanUrl = shopUrl.endsWith("/") ? shopUrl.substring(0, shopUrl.length() - 1) : shopUrl;
        HttpUrl url = HttpUrl.parse(cleanUrl + "/list/open_api.html").newBuilder()
                .addQueryParameter("mode", "search")
                .addQueryParameter("type", "product")
                .addQueryParameter("limit", String.valueOf(limit))
                .addQueryParameter("page", String.valueOf(page))
                .build();

        Request req = new Request.Builder()
                .url(url)
                .get()
                .header("Shopkey", shopKey)
                .header("Licensekey", licenseKey)
                .header("User-Agent", UA)
                .header("Accept", "*/*")
                .header("Cache-Control", "no-cache")
                .build();

        log.info("[Makeshop] GET {} (shopKey={}, licenseKey={})", url, mask(shopKey), mask(licenseKey));
        try (Response res = http.newCall(req).execute()) {
            String body = res.body() != null ? res.body().string() : "";
            log.info("[Makeshop] HTTP {} ({} bytes) — first 400 chars: {}",
                    res.code(), body.length(), body.substring(0, Math.min(400, body.length())));
            if (!res.isSuccessful()) {
                throw new IOException("makeshop API failed: HTTP " + res.code() + " body=" + body.substring(0, Math.min(200, body.length())));
            }
            JsonNode root = mapper.readTree(body);
            int total = root.path("totalCount").asInt(0);
            int totalPage = root.path("totalPage").asInt(0);
            int count = root.path("count").asInt(0);
            JsonNode list = root.path("list");
            return new ProductListResponse(total, totalPage, count, list);
        }
    }

    private static String mask(String s) {
        if (s == null || s.isEmpty()) return "(null)";
        if (s.length() <= 6) return "*".repeat(s.length());
        return s.substring(0, 3) + "…" + s.substring(s.length() - 3);
    }

    public int fetchTotalCount(String shopUrl, String shopKey, String licenseKey) throws IOException {
        return fetchProducts(shopUrl, shopKey, licenseKey, 1, 1).total();
    }

    /**
     * uids[] 여러 개 + fields=product_content,uid 로 한 번에 N개 상품 본문 조회.
     * 메이크샵 OpenAPI 한도: 한 번에 최대 200 uids.
     * 응답 list 각 항목의 product_content는 base64 인코딩된 HTML.
     */
    public JsonNode fetchProductContents(String shopUrl, String shopKey, String licenseKey,
                                          java.util.List<String> uids, String fields) throws IOException {
        String cleanUrl = shopUrl.endsWith("/") ? shopUrl.substring(0, shopUrl.length() - 1) : shopUrl;
        HttpUrl.Builder b = HttpUrl.parse(cleanUrl + "/list/open_api.html").newBuilder()
                .addQueryParameter("mode", "search")
                .addQueryParameter("type", "product")
                .addQueryParameter("fields", fields == null || fields.isBlank() ? "product_content,uid" : fields);
        for (String uid : uids) b.addEncodedQueryParameter("uids[]", uid);
        HttpUrl url = b.build();

        Request req = new Request.Builder()
                .url(url)
                .get()
                .header("Shopkey", shopKey)
                .header("Licensekey", licenseKey)
                .header("User-Agent", UA)
                .header("Accept", "*/*")
                .build();
        try (Response res = http.newCall(req).execute()) {
            if (!res.isSuccessful() || res.body() == null) {
                throw new IOException("makeshop content API failed: HTTP " + res.code());
            }
            return mapper.readTree(res.body().byteStream());
        }
    }
}
