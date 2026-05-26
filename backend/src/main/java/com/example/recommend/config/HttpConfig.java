package com.example.recommend.config;

import okhttp3.OkHttpClient;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.time.Duration;

@Configuration
@EnableConfigurationProperties(AppProperties.class)
public class HttpConfig {

    @Bean
    public OkHttpClient embedderHttp(AppProperties props) {
        Duration t = Duration.ofMillis(props.embedder().timeoutMs());
        return new OkHttpClient.Builder()
                .connectTimeout(t)
                .readTimeout(t)
                .writeTimeout(t)
                .build();
    }

    @Bean
    public OkHttpClient llmHttp(AppProperties props) {
        Duration t = Duration.ofMillis(props.llm().timeoutMs());
        return new OkHttpClient.Builder()
                .connectTimeout(t)
                .readTimeout(t)
                .writeTimeout(t)
                .build();
    }

    // ObjectMapper는 Spring Boot가 자동 구성한 걸 사용 (JavaTimeModule 포함 — OffsetDateTime 직렬화 OK)
}
