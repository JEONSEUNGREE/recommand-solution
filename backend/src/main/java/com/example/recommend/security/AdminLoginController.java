package com.example.recommend.security;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/**
 * admin 패널 로그인.
 *
 * env로 {@code ADMIN_USERNAME} / {@code ADMIN_PASSWORD} 를 미리 받아두고, 일치하면 API 토큰을 응답.
 * (운영자 1명 짜리 패널이라 사용자 테이블/세션 도입은 과함 — issue acceptance criteria #5 "최소 BasicAuth or session 1 layer" 충족.)
 *
 * 응답으로 받은 토큰을 admin 페이지가 {@code localStorage.aira_token} 으로 저장하고
 * 이후 모든 fetch에 {@code Authorization: Bearer ...} 로 첨부한다.
 */
@RestController
public class AdminLoginController {

    private static final Logger log = LoggerFactory.getLogger(AdminLoginController.class);

    private final String adminUsername;
    private final String adminPassword;
    private final String apiToken;

    public AdminLoginController(@Value("${app.security.admin.username:}") String adminUsername,
                                @Value("${app.security.admin.password:}") String adminPassword,
                                @Value("${app.security.api-token:}") String apiToken) {
        this.adminUsername = adminUsername == null ? "" : adminUsername.trim();
        this.adminPassword = adminPassword == null ? "" : adminPassword;
        this.apiToken = apiToken == null ? "" : apiToken.trim();
    }

    public record LoginRequest(String username, String password) {}

    @PostMapping("/admin/login")
    public ResponseEntity<Map<String, Object>> login(@RequestBody LoginRequest req) {
        if (adminUsername.isEmpty() || adminPassword.isEmpty() || apiToken.isEmpty()) {
            log.warn("[AdminLogin] admin 자격증명/토큰 미설정 — login 거부");
            return ResponseEntity.status(503).body(Map.of("error", "admin auth not configured"));
        }
        String u = req == null ? null : req.username();
        String p = req == null ? null : req.password();
        if (u == null || p == null || !constantTimeEquals(u, adminUsername) || !constantTimeEquals(p, adminPassword)) {
            // 사용자 enumeration 방지 — 동일 메시지
            return ResponseEntity.status(401).body(Map.of("error", "invalid credentials"));
        }
        return ResponseEntity.ok(Map.of("token", apiToken));
    }

    private static boolean constantTimeEquals(String a, String b) {
        if (a == null || b == null) return false;
        if (a.length() != b.length()) return false;
        int diff = 0;
        for (int i = 0; i < a.length(); i++) diff |= a.charAt(i) ^ b.charAt(i);
        return diff == 0;
    }
}
