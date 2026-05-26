/* AIRA admin · shared auth bootstrap.
 *
 * 책임:
 *   1. localStorage.aira_token 이 없으면 login.html 로 리다이렉트.
 *   2. window.fetch 를 패치해 backend(:8090) / embedder(:8001) 호스트로 가는 요청에
 *      Authorization: Bearer <aira_token> 를 자동 첨부.
 *   3. 401 응답을 받으면 토큰을 비우고 login.html 로 다시 리다이렉트.
 *
 * 모든 admin 페이지에서 `<script src="auth.js"></script>` 를 가장 먼저 로드해야 한다.
 * login.html 은 이 스크립트를 로드하지 않는다 (자체 폼이 토큰을 발급받음).
 */
(function () {
  'use strict';

  // depth: 서브디렉토리 페이지(sync/*.html)는 body[data-depth="1"] 로 표시 → login 경로를 ../login.html 로.
  function loginPath() {
    try {
      var depth = parseInt(document.body && document.body.dataset && document.body.dataset.depth || '0', 10);
      return ('../'.repeat(depth || 0)) + 'login.html';
    } catch (_) { return 'login.html'; }
  }

  function getToken() {
    try { return localStorage.getItem('aira_token') || ''; } catch (_) { return ''; }
  }

  function clearTokenAndRedirect() {
    try { localStorage.removeItem('aira_token'); } catch (_) {}
    location.href = loginPath();
  }

  // 토큰이 없으면 즉시 login 으로.
  // login.html 자체는 이 스크립트를 로드하지 않으므로 무한루프는 없음.
  if (!getToken()) {
    clearTokenAndRedirect();
    // 페이지 로드를 막기 위해 throw — 후속 inline 스크립트가 fetch 호출하기 전에 정지.
    throw new Error('auth required');
  }

  /* ---------- fetch 패치 ---------- */
  var PROTECTED_HOSTS = [
    // 절대 URL 매칭
    'http://localhost:8001', 'http://127.0.0.1:8001',
    'http://localhost:8090', 'http://127.0.0.1:8090',
  ];

  function shouldAttachAuth(input) {
    try {
      var url = typeof input === 'string' ? input : (input && input.url) || '';
      if (!url) return true; // 상대 URL — 같은 호스트로 가정해서 첨부
      // 같은 origin
      if (url.startsWith('/')) return true;
      for (var i = 0; i < PROTECTED_HOSTS.length; i++) {
        if (url.indexOf(PROTECTED_HOSTS[i]) === 0) return true;
      }
      return false;
    } catch (_) {
      return false;
    }
  }

  var origFetch = window.fetch.bind(window);
  window.fetch = function patchedFetch(input, init) {
    var token = getToken();
    if (!token) {
      clearTokenAndRedirect();
      return Promise.reject(new Error('no auth token'));
    }
    if (shouldAttachAuth(input)) {
      init = init || {};
      var headers;
      if (init.headers instanceof Headers) {
        headers = init.headers;
        if (!headers.has('Authorization')) headers.set('Authorization', 'Bearer ' + token);
      } else if (Array.isArray(init.headers)) {
        var hasAuth = init.headers.some(function (h) { return h && h[0] && String(h[0]).toLowerCase() === 'authorization'; });
        if (!hasAuth) init.headers = init.headers.concat([['Authorization', 'Bearer ' + token]]);
      } else {
        headers = Object.assign({}, init.headers || {});
        var hasAuthKey = Object.keys(headers).some(function (k) { return k.toLowerCase() === 'authorization'; });
        if (!hasAuthKey) headers['Authorization'] = 'Bearer ' + token;
        init.headers = headers;
      }
    }
    return origFetch(input, init).then(function (res) {
      if (res && res.status === 401) {
        // 토큰 무효 — 강제 재로그인
        clearTokenAndRedirect();
      }
      return res;
    });
  };

  // 편의 헬퍼: 다른 스크립트에서 토큰 접근.
  window.AiraAuth = {
    getToken: getToken,
    logout: clearTokenAndRedirect,
  };
})();
