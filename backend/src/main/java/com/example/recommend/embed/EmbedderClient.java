package com.example.recommend.embed;

import com.example.recommend.config.AppProperties;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.*;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.util.List;

/** 자연어 텍스트 → 벡터로 인코딩하는 사이드카 클라이언트. */
@Component
public class EmbedderClient {

    private static final MediaType JSON = MediaType.parse("application/json");

    private final OkHttpClient http;
    private final ObjectMapper mapper;
    private final String baseUrl;
    private final String apiToken;

    public EmbedderClient(@Qualifier("embedderHttp") OkHttpClient http,
                          ObjectMapper mapper,
                          AppProperties props) {
        this.http = http;
        this.mapper = mapper;
        this.baseUrl = props.embedder().baseUrl();
        this.apiToken = props.embedder().apiToken();
    }

    public float[] embed(String text) {
        try {
            String body = mapper.writeValueAsString(java.util.Map.of("texts", List.of(text)));
            Request.Builder rb = new Request.Builder()
                    .url(baseUrl + "/embed")
                    .post(RequestBody.create(body, JSON));
            if (apiToken != null && !apiToken.isBlank()) {
                rb.header("Authorization", "Bearer " + apiToken);
            }
            Request req = rb.build();
            try (Response res = http.newCall(req).execute()) {
                if (!res.isSuccessful()) {
                    throw new RuntimeException("Embedder failed: " + res.code());
                }
                JsonNode root = mapper.readTree(res.body().byteStream());
                JsonNode vec = root.get("vectors").get(0);
                float[] out = new float[vec.size()];
                for (int i = 0; i < vec.size(); i++) {
                    out[i] = (float) vec.get(i).asDouble();
                }
                return out;
            }
        } catch (IOException e) {
            throw new RuntimeException("Failed to embed: " + e.getMessage(), e);
        }
    }
}
