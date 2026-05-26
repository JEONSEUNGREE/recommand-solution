/* AIRA admin · shared sidebar + topbar partial
   Each page sets <body data-route="..." data-crumbs="홈 / 상품 데이터 / 추출 리스트">.
   `data-crumbs` items separated by " / ". The last is the current page.
   Sub-directory pages set data-depth="1" so all hrefs are prefixed with "../".
*/
(function () {
  const ROUTE_MAP = {
    'dashboard':         { href: 'dashboard.html',          crumb: ['홈', '대시보드'] },
    'upload':            { href: 'upload.html',             crumb: ['홈', '상품 데이터', '상품 업로드'] },
    'extractions':       { href: 'extractions.html',        crumb: ['홈', '검색 로그'] },
    'result':            { href: 'result.html',             crumb: ['홈', '상품 데이터', '추출 결과'] },
    'products':          { href: 'products.html',           crumb: ['홈', '상품 데이터', '상품 목록'] },
    'product':           { href: 'product.html',            crumb: ['홈', '상품 데이터', '상품 목록', '상품 상세'] },
    'chat':              { href: 'chat.html',               crumb: ['홈', '추천 & 분석', '추천 채팅'] },
    'cost':              { href: 'cost.html',               crumb: ['홈', '추천 & 분석', '토큰·비용'] },
    'sync-advertisers':  { href: 'sync/index.html',         crumb: ['홈', '운영', '광고주 목록'] },
    'sync-products':     { href: 'sync/products.html',      crumb: ['홈', '운영', '광고주 목록', '상품 정제'] },
  };

  function getAdv() {
    try { return JSON.parse(localStorage.getItem('aira_adv') || 'null'); } catch { return null; }
  }

  const ICONS = {
    dashboard:   '<svg viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/></svg>',
    upload:      '<svg viewBox="0 0 24 24"><path d="M12 16V4M7 9l5-5 5 5M5 20h14"/></svg>',
    extractions: '<svg viewBox="0 0 24 24"><path d="M8 6h13M8 12h13M8 18h13"/><circle cx="4" cy="6" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/></svg>',
    products:    '<svg viewBox="0 0 24 24"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V10"/></svg>',
    chat:        '<svg viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>',
    cost:        '<svg viewBox="0 0 24 24"><path d="M22 12h-4l-3 9-6-18-3 9H2"/></svg>',
    ws:          '<svg viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 3v18"/></svg>',
    perm:        '<svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 21v-2a4 4 0 0 1 4-4h8a4 4 0 0 1 4 4v2"/></svg>',
    refresh:     '<svg viewBox="0 0 24 24"><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/><path d="M8 16H3v5"/></svg>',
    bell:        '<svg viewBox="0 0 24 24"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/></svg>',
    help:        '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M9.1 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
    sync:        '<svg viewBox="0 0 24 24"><path d="M4 4h6v6H4z"/><path d="M14 4h6v6h-6z"/><path d="M4 14h6v6H4z"/><path d="M17 17m-3 0a3 3 0 1 0 6 0a3 3 0 1 0-6 0"/></svg>',
    store:       '<svg viewBox="0 0 24 24"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>',
  };

  function buildItem(key, label, badge, active, base) {
    const href = (base || '') + (ROUTE_MAP[key]?.href || '#');
    const ic = ICONS[key] || ICONS['store'] || '';
    return `<a class="item ${active ? 'active' : ''}" href="${href}">
      <span class="ic">${ic}</span>${label}${badge != null ? `<span class="cnt">${badge}</span>` : ''}
    </a>`;
  }

  function buildSidebar(activeRoute, base) {
    return `
      <aside class="side">
        <div class="brand">
          <div class="logo">A</div>
          <div class="nm">AI-SEARCH<small>AI 추천 솔루션</small></div>
        </div>

        <div class="grp">
          <div class="grp-lbl">개요</div>
          ${buildItem('dashboard', '대시보드', null, activeRoute === 'dashboard', base)}
        </div>

        <div class="grp">
          <div class="grp-lbl">상품 데이터</div>
          ${buildItem('products',    '상품 목록',    null, activeRoute === 'products' || activeRoute === 'product', base)}
          ${buildItem('extractions', '검색 로그',    null, activeRoute === 'extractions', base)}
        </div>

        <div class="grp">
          <div class="grp-lbl">추천 & 분석</div>
          ${buildItem('chat', '추천 채팅',  null, activeRoute === 'chat', base)}
          ${buildItem('cost', '토큰·비용',  null, activeRoute === 'cost', base)}
        </div>

        <div class="grp">
          <div class="grp-lbl">운영 · 동기화</div>
          ${buildItem('sync-advertisers', '광고주 목록', null, activeRoute === 'sync-advertisers' || activeRoute === 'sync-products', base)}
        </div>

        <div class="grp">
          <div class="grp-lbl">설정</div>
          <a class="item" href="#"><span class="ic">${ICONS.ws}</span>워크스페이스</a>
          <a class="item" href="#"><span class="ic">${ICONS.perm}</span>계정·권한</a>
        </div>

        <div class="side-spacer"></div>

        <div class="side-foot">
          <div class="row"><span>광고주</span><b id="side-adv-name">—</b></div>
          <div class="row"><span>버전</span><span class="ver">v0.4-poc</span></div>
          <div class="row"><a href="${(base||'')+'login.html'}" style="font-size:11px;color:var(--text-4);text-decoration:none;" onmouseover="this.style.color='var(--primary)'" onmouseout="this.style.color='var(--text-4)'">광고주 변경 →</a></div>
        </div>
      </aside>
    `;
  }

  function buildTopbar(crumbsRaw) {
    let crumbs;
    if (crumbsRaw) {
      crumbs = crumbsRaw.split(' / ').map((c) => c.trim());
    } else {
      crumbs = ['홈'];
    }
    const crumbHtml = crumbs.map((c, i) => {
      const last = i === crumbs.length - 1;
      const sep = i > 0 ? '<span class="sep">/</span>' : '';
      return sep + (last
        ? `<span class="here">${c}</span>`
        : `<a href="${i === 0 ? 'dashboard.html' : '#'}">${c}</a>`);
    }).join('');

    return `
      <div class="topbar">
        <div class="crumbs">${crumbHtml}</div>
        <div class="top-right">
          <button class="top-icon" title="새로고침">${ICONS.refresh}</button>
          <button class="top-icon" title="알림" data-badge="1">${ICONS.bell}</button>
          <button class="top-icon" title="도움말">${ICONS.help}</button>
          <div class="top-user">
            <div class="nm-t">루브르파리<small>관리자</small></div>
          </div>
        </div>
      </div>
    `;
  }

  function injectFonts() {
    // Pretendard is loaded via <link> in each page already.
    // Inject Source Serif 4 + JetBrains Mono once.
    if (document.getElementById('aira-fonts')) return;
    const pre1 = document.createElement('link');
    pre1.rel = 'preconnect';
    pre1.href = 'https://fonts.googleapis.com';
    document.head.appendChild(pre1);
    const pre2 = document.createElement('link');
    pre2.rel = 'preconnect';
    pre2.href = 'https://fonts.gstatic.com';
    pre2.crossOrigin = 'anonymous';
    document.head.appendChild(pre2);
    const link = document.createElement('link');
    link.id = 'aira-fonts';
    link.rel = 'stylesheet';
    link.href = 'https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&family=Source+Serif+4:ital,wght@0,400;0,500;0,600;0,700;1,400;1,500&display=swap';
    document.head.appendChild(link);
  }

  function init() {
    injectFonts();
    const body = document.body;

    // Embed mode: when this page is loaded inside an iframe (e.g. the dashboard
    // "상품 가져오기" 탭) or with ?embed=1, drop the sidebar/topbar chrome so it
    // integrates cleanly into the host page instead of nesting a second shell.
    let isEmbed;
    try {
      isEmbed = new URLSearchParams(location.search).has('embed') || window.self !== window.top;
    } catch (_) {
      isEmbed = true; // cross-origin frame access throws → we're embedded
    }
    if (isEmbed) {
      body.classList.add('embed');
      const sm = document.getElementById('side-mount');
      const tm = document.getElementById('top-mount');
      if (sm) sm.remove();
      if (tm) tm.remove();
      return;
    }

    const route = body.dataset.route || '';
    const crumbsAttr = body.dataset.crumbs;
    const depth = parseInt(body.dataset.depth || '0', 10);
    const base = '../'.repeat(depth);

    // Auto-derive crumbs from route map if not explicitly given
    let crumbStr = crumbsAttr;
    if (!crumbStr && ROUTE_MAP[route]) {
      crumbStr = ROUTE_MAP[route].crumb.join(' / ');
    }

    const sideMount = document.getElementById('side-mount');
    const topMount  = document.getElementById('top-mount');
    if (sideMount) sideMount.outerHTML = buildSidebar(route, base);
    if (topMount)  topMount.outerHTML  = buildTopbar(crumbStr);

    // 광고주명 sidebar에 반영
    const adv = getAdv();
    const nameEl = document.getElementById('side-adv-name');
    if (nameEl) nameEl.textContent = adv ? `#${adv.id} ${adv.name}` : '—';
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
