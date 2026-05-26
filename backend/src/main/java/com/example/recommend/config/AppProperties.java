package com.example.recommend.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "app")
public record AppProperties(Embedder embedder, Llm llm) {

    public record Embedder(String baseUrl, long timeoutMs, String apiToken) {}

    /**
     * LLM 설정. OpenAI 호환 Chat Completions 형식 (Groq, OpenAI, Together, OpenRouter 등 사용 가능).
     */
    public record Llm(String baseUrl, String apiKey, String model, long timeoutMs) {}
}
