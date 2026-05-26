/* AIRA admin · enhance.js
   - Count-up for .stat .v values
   - Sliding indicator under .tabs (reacts to .active class changes)
   - Subtle reveal on cards
*/
(function () {
  'use strict';

  // ----- count-up -----
  function animateValue(el) {
    // animate the first numeric run within the first text node; leave <small> children alone
    const nodes = [...el.childNodes];
    const tn = nodes.find((n) => n.nodeType === 3 && /\d/.test(n.textContent));
    if (!tn) return;
    const original = tn.textContent;
    const m = original.match(/^(\s*[^\d.\-]*?)([\d,]+(?:\.\d+)?)(.*)$/);
    if (!m) return;
    const prefix = m[1];
    const numStr = m[2];
    const suffix = m[3];
    const target = parseFloat(numStr.replace(/,/g, ''));
    if (isNaN(target)) return;
    const decimals = numStr.includes('.') ? numStr.split('.')[1].length : 0;
    const useCommas = numStr.includes(',');
    const fmt = (v) => {
      if (decimals > 0) return v.toFixed(decimals);
      const rounded = Math.round(v);
      return useCommas ? rounded.toLocaleString('en-US') : String(rounded);
    };
    const duration = 700 + Math.random() * 200;
    const startAt = performance.now();
    tn.textContent = prefix + fmt(0) + suffix;
    function tick(now) {
      const p = Math.min(1, (now - startAt) / duration);
      const eased = 1 - Math.pow(1 - p, 3);
      tn.textContent = prefix + fmt(target * eased) + suffix;
      if (p < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }

  // ----- tabs sliding indicator -----
  function initTabs(tabs) {
    function update() {
      const a = tabs.querySelector('.tab.active');
      if (!a) return;
      const tr = tabs.getBoundingClientRect();
      const ar = a.getBoundingClientRect();
      tabs.style.setProperty('--ind-l', (ar.left - tr.left) + 'px');
      tabs.style.setProperty('--ind-w', ar.width + 'px');
    }
    update();
    const obs = new MutationObserver(update);
    tabs.querySelectorAll('.tab').forEach((t) => obs.observe(t, { attributes: true, attributeFilter: ['class'] }));
    window.addEventListener('resize', update);
    // re-measure once fonts settle (serif width changes)
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(update);
    }
    setTimeout(update, 300);
  }

  function init() {
    document.querySelectorAll('.stat .v').forEach(animateValue);
    document.querySelectorAll('.tabs').forEach(initTabs);

    // Re-trigger row-in animation if rows were added later
    // (handled by CSS keyframes on initial render)

    // Press scale for all buttons (already in CSS as :active)
    // Mark fonts ready
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(() => document.body.classList.add('fonts-ready'));
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    // already parsed — defer a tick so page-specific scripts have run
    setTimeout(init, 0);
  }
})();
