package com.example.recommend.security;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.Set;

/**
 * 모든 요청에 {@code Authorization: Bearer <API_TOKEN>} 을 강제하는 shared-secret gate.
 *
 * 예외:
 * <ul>
 *   <li>{@code /healthz} (헬스체크) — 모니터링용</li>
 *   <li>{@code /admin/login} — 자격증명 검증 후 토큰을 발급하는 endpoint 자체</li>
 *   <li>{@code OPTIONS} — CORS preflight</li>
 *   <li>광고주 이미지 정적 서빙 ({@code /advertisers/&#42;/products/&#42;/images/&#42;}) — 프론트가 검색 결과 카드에 그대로 박는 이미지라 토큰을 붙일 방법이 없음.
 *       토큰화하려면 별도 signed-URL 도입이 필요해 후속 child issue로 분리.</li>
 * </ul>
 *
 * 토큰 미설정 시(빈 문자열) — 운영 사고 방지를 위해 fail-closed로 503을 응답한다.
 * 단, {@code app.security.allow-unauthenticated-when-token-missing=true} (기본 false)면 인증을 우회한다 (dev 전용).
 */
@Component
public class BearerAuthFilter extends OncePerRequestFilter {

    private static final Logger log = LoggerFactory.getLogger(BearerAuthFilter.class);

    private static final Set<String> EXEMPT_PATHS = Set.of(
            "/healthz",
            "/admin/login"
    );

    private final String apiToken;
    private final boolean allowWhenMissing;

    public BearerAuthFilter(@Value("${app.security.api-token:}") String apiToken,
                            @Value("${app.security.allow-unauthenticated-when-token-missing:false}") boolean allowWhenMissing) {
        this.apiToken = apiToken == null ? "" : apiToken.trim();
        this.allowWhenMissing = allowWhenMissing;
        if (this.apiToken.isEmpty()) {
            if (allowWhenMissing) {
                log.warn("[BearerAuthFilter] API_TOKEN 미설정 — dev 모드(allow-unauthenticated-when-token-missing=true)로 모든 요청 허용. 프로덕션 절대 금지.");
            } else {
                log.error("[BearerAuthFilter] API_TOKEN 미설정 — 모든 보호된 endpoint가 503을 응답합니다. env APP_API_TOKEN 을 설정하세요.");
            }
        } else {
            log.info("[BearerAuthFilter] Bearer auth enabled (token length={} chars)", this.apiToken.length());
        }
    }

    @Override
    protected void doFilterInternal(HttpServletRequest req, HttpServletResponse res, FilterChain chain)
            throws ServletException, IOException {

        // 1) CORS preflight
        if ("OPTIONS".equalsIgnoreCase(req.getMethod())) {
            chain.doFilter(req, res);
            return;
        }

        // 2) 예외 path
        String path = req.getRequestURI();
        if (isExempt(path)) {
            chain.doFilter(req, res);
            return;
        }

        // 3) 토큰 미설정 — fail-closed
        if (apiToken.isEmpty()) {
            if (allowWhenMissing) {
                chain.doFilter(req, res);
                return;
            }
            writeJson(res, 503, "{\"error\":\"server auth not configured\"}");
            return;
        }

        // 4) Bearer 검사
        String header = req.getHeader("Authorization");
        if (header == null || header.length() < 8 || !header.regionMatches(true, 0, "Bearer ", 0, 7)) {
            writeJson(res, 401, "{\"error\":\"missing bearer token\"}");
            return;
        }
        String presented = header.substring(7).trim();
        if (!constantTimeEquals(presented, apiToken)) {
            writeJson(res, 401, "{\"error\":\"invalid bearer token\"}");
            return;
        }
        chain.doFilter(req, res);
    }

    private static boolean isExempt(String path) {
        if (path == null) return false;
        if (EXEMPT_PATHS.contains(path)) return true;
        // 광고주 이미지 정적 서빙은 토큰화가 어려워 우회. (TODO: signed URL 도입)
        if (path.startsWith("/advertisers/") && path.contains("/products/") && path.contains("/images/")) {
            return true;
        }
        return false;
    }

    private static void writeJson(HttpServletResponse res, int status, String body) throws IOException {
        res.setStatus(status);
        res.setContentType("application/json; charset=utf-8");
        res.getWriter().write(body);
    }

    private static boolean constantTimeEquals(String a, String b) {
        if (a == null || b == null) return false;
        if (a.length() != b.length()) return false;
        int diff = 0;
        for (int i = 0; i < a.length(); i++) diff |= a.charAt(i) ^ b.charAt(i);
        return diff == 0;
    }
}
