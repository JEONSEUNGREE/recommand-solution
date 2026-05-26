package com.example.recommend.config;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.CorsRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

@Configuration
public class CorsConfig implements WebMvcConfigurer {

    private static final Logger log = LoggerFactory.getLogger(CorsConfig.class);

    private final String[] origins;

    public CorsConfig(@Value("${app.security.cors-origins:http://localhost:*,http://127.0.0.1:*,http://192.168.*.*:*,http://10.*.*.*:*,http://172.16.*.*:*}") String origins) {
        this.origins = splitCsv(origins);
        log.info("[CorsConfig] allowedOriginPatterns = {}", String.join(", ", this.origins));
    }

    @Override
    public void addCorsMappings(CorsRegistry registry) {
        registry.addMapping("/**")
                .allowedOriginPatterns(origins)
                .allowedMethods("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
                .allowedHeaders("*");
    }

    private static String[] splitCsv(String s) {
        if (s == null || s.isBlank()) return new String[0];
        String[] parts = s.split(",");
        for (int i = 0; i < parts.length; i++) parts[i] = parts[i].trim();
        return parts;
    }
}
