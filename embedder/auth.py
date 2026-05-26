"""Embedder bearer auth middleware.

모든 endpoint를 `Authorization: Bearer ${API_TOKEN}` 게이트로 보호한다.

예외:
  - /healthz (헬스체크)
  - OPTIONS (CORS preflight — CORSMiddleware가 알아서 처리)

토큰 미설정 시:
  - APP_ALLOW_UNAUTHENTICATED=true 면 (dev) 그대로 통과 + WARN 로그.
  - 그 외에는 fail-closed — 보호 endpoint가 503을 반환.
"""
from __future__ import annotations

import logging
import os
from typing import Iterable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


log = logging.getLogger("embedder.auth")

# 토큰 검증을 우회할 path. 정확히 일치하는 경우만.
EXEMPT_PATHS: frozenset[str] = frozenset({"/healthz", "/docs", "/openapi.json", "/redoc"})


def _constant_time_equals(a: str, b: str) -> bool:
    if a is None or b is None or len(a) != len(b):
        return False
    diff = 0
    for x, y in zip(a, b):
        diff |= ord(x) ^ ord(y)
    return diff == 0


class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, token: str, allow_unauthenticated: bool):
        super().__init__(app)
        self.token = (token or "").strip()
        self.allow_unauthenticated = bool(allow_unauthenticated)
        if not self.token:
            if self.allow_unauthenticated:
                log.warning(
                    "[BearerAuth] API_TOKEN 미설정 — dev 모드(APP_ALLOW_UNAUTHENTICATED=true)로 모든 요청 허용. 프로덕션 절대 금지."
                )
            else:
                log.error(
                    "[BearerAuth] API_TOKEN 미설정 — 모든 보호된 endpoint가 503을 반환합니다. env APP_API_TOKEN 을 설정하세요."
                )
        else:
            log.info("[BearerAuth] enabled (token length=%d chars)", len(self.token))

    async def dispatch(self, request: Request, call_next):
        # CORS preflight — CORSMiddleware 가 응답.
        if request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path or ""
        if path in EXEMPT_PATHS:
            return await call_next(request)

        if not self.token:
            if self.allow_unauthenticated:
                return await call_next(request)
            return JSONResponse({"error": "server auth not configured"}, status_code=503)

        auth = request.headers.get("authorization") or ""
        if not auth.lower().startswith("bearer "):
            return JSONResponse({"error": "missing bearer token"}, status_code=401)
        presented = auth[7:].strip()
        if not _constant_time_equals(presented, self.token):
            return JSONResponse({"error": "invalid bearer token"}, status_code=401)
        return await call_next(request)


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def cors_origins_from_env() -> list[str]:
    """CORS_ORIGINS env 를 파싱. 빈 값이면 fail-closed (빈 리스트)."""
    return _parse_csv(os.getenv("CORS_ORIGINS"))


def cors_origins_with_dev_fallback(default: Iterable[str]) -> list[str]:
    """CORS_ORIGINS 미설정 시 default 사용 — dev 편의용."""
    parsed = cors_origins_from_env()
    return parsed if parsed else list(default)


def install_auth(app) -> None:
    """app에 BearerAuthMiddleware를 등록. server.py에서 호출."""
    token = os.getenv("APP_API_TOKEN", "")
    allow_unauth = (os.getenv("APP_ALLOW_UNAUTHENTICATED", "true").lower()
                    in {"1", "true", "yes", "on"})
    app.add_middleware(
        BearerAuthMiddleware,
        token=token,
        allow_unauthenticated=allow_unauth,
    )
