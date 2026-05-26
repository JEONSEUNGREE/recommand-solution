package com.example.recommend.api;

import com.example.recommend.config.AppProperties;
import jakarta.validation.constraints.NotBlank;
import okhttp3.*;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

import java.io.IOException;
import java.time.Duration;
import java.util.Objects;

/**
 * 상품 상세 페이지 URL을 받아 embedder 사이드카의 /scrape를 호출하고 결과를 그대로 반환.
 * OCR이 들어가 응답이 분 단위로 길어질 수 있어 별도의 긴 타임아웃을 가진 클라이언트를 사용.
 */
@RestController
public class EnrichController {

    private static final okhttp3.MediaType JSON = okhttp3.MediaType.parse("application/json");
    private final OkHttpClient http;
    private final String embedderBase;
    private final String apiToken;

    public EnrichController(AppProperties props) {
        this.embedderBase = props.embedder().baseUrl();
        this.apiToken = props.embedder().apiToken();
        Duration t = Duration.ofMinutes(5);
        this.http = new OkHttpClient.Builder()
                .connectTimeout(t).readTimeout(t).writeTimeout(t).build();
    }

    public record EnrichRequest(@NotBlank String url, Integer maxImages, Boolean ocr) {}

    @PostMapping(value = "/enrich", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<String> enrich(@RequestBody EnrichRequest req) throws IOException {
        // 사이드카에 그대로 전달 — JSON 직렬화는 수동(작은 페이로드라 의존성 최소화)
        String body = String.format(
                "{\"url\":%s,\"max_images\":%d,\"ocr\":%b}",
                quote(req.url()),
                req.maxImages() == null ? 8 : req.maxImages(),
                req.ocr() == null ? true : req.ocr()
        );
        Request.Builder rb = new Request.Builder()
                .url(embedderBase + "/scrape")
                .post(okhttp3.RequestBody.create(body, JSON));
        if (apiToken != null && !apiToken.isBlank()) {
            rb.header("Authorization", "Bearer " + apiToken);
        }
        Request out = rb.build();
        try (Response res = http.newCall(out).execute()) {
            String text = Objects.requireNonNull(res.body()).string();
            return ResponseEntity.status(res.code())
                    .header("Content-Type", "application/json; charset=utf-8")
                    .body(text);
        }
    }

    private static String quote(String s) {
        StringBuilder sb = new StringBuilder("\"");
        for (char c : s.toCharArray()) {
            switch (c) {
                case '\\', '"' -> sb.append('\\').append(c);
                case '\n' -> sb.append("\\n");
                case '\r' -> sb.append("\\r");
                case '\t' -> sb.append("\\t");
                default -> {
                    if (c < 0x20) sb.append(String.format("\\u%04x", (int) c));
                    else sb.append(c);
                }
            }
        }
        return sb.append('"').toString();
    }
}
