package com.example.recommend.llm;

import com.example.recommend.config.AppProperties;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import okhttp3.*;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.stereotype.Component;

import java.io.IOException;

/**
 * OpenAI 호환 Chat Completions 클라이언트.
 * Groq / OpenAI / Together / OpenRouter 등 어디서든 동작.
 *
 *   POST {baseUrl}/chat/completions
 *   Authorization: Bearer {apiKey}
 *   {"model":..., "messages":[{role,content}], "max_tokens":...}
 */
@Component
public class LlmClient {

    private static final Logger log = LoggerFactory.getLogger(LlmClient.class);
    private static final MediaType JSON = MediaType.parse("application/json");

    private final OkHttpClient http;
    private final ObjectMapper mapper;
    private final AppProperties.Llm cfg;

    public LlmClient(@Qualifier("llmHttp") OkHttpClient http,
                     ObjectMapper mapper,
                     AppProperties props) {
        this.http = http;
        this.mapper = mapper;
        this.cfg = props.llm();
    }

    /** system prompt + 1턴 user 메시지 → 응답 텍스트. */
    public String chat(String systemPrompt, String userText, int maxTokens) {
        if (cfg.apiKey() == null || cfg.apiKey().isBlank()) {
            throw new IllegalStateException("LLM API key is not configured (set GROQ_API_KEY)");
        }

        ObjectNode body = mapper.createObjectNode();
        body.put("model", cfg.model());
        body.put("max_tokens", maxTokens);
        body.put("temperature", 0.2);
        ArrayNode messages = body.putArray("messages");
        messages.addObject().put("role", "system").put("content", systemPrompt);
        messages.addObject().put("role", "user").put("content", userText);

        Request req = new Request.Builder()
                .url(cfg.baseUrl() + "/chat/completions")
                .header("Authorization", "Bearer " + cfg.apiKey())
                .post(RequestBody.create(body.toString(), JSON))
                .build();

        try (Response res = http.newCall(req).execute()) {
            String respBody = res.body() != null ? res.body().string() : "";
            if (!res.isSuccessful()) {
                log.error("LLM error {}: {}", res.code(), respBody);
                throw new RuntimeException("LLM call failed: " + res.code());
            }
            JsonNode root = mapper.readTree(respBody);
            return root.get("choices").get(0).get("message").get("content").asText();
        } catch (IOException e) {
            throw new RuntimeException("LLM IO error: " + e.getMessage(), e);
        }
    }

    /** ```json ... ``` 코드펜스 제거. */
    public static String stripFence(String text) {
        String t = text.strip();
        if (t.startsWith("```")) {
            int firstNl = t.indexOf('\n');
            if (firstNl > 0) t = t.substring(firstNl + 1);
            if (t.endsWith("```")) t = t.substring(0, t.length() - 3);
        }
        return t.strip();
    }
}
