// Responsive behaviour shared by the app shell: mobile/tablet drawer navigation,
// keyboard-accessible clickable rows, and TV / remote-control (D-pad) navigation.
(function () {
  'use strict';
  const shell = document.querySelector('.shell');
  const burger = document.getElementById('menuBtn');
  const nav = document.getElementById('nav');
  const drawerMQ = window.matchMedia('(max-width: 1023px)');

  // ---- drawer navigation (tablet & phone) --------------------------------
  let backdrop = null;
  function setNav(open) {
    if (!shell) return;
    shell.classList.toggle('nav-open', open);
    document.body.classList.toggle('nav-locked', open && drawerMQ.matches);
    if (burger) burger.setAttribute('aria-expanded', String(open));
    if (open && drawerMQ.matches) {
      if (!backdrop) {
        backdrop = document.createElement('div');
        backdrop.className = 'nav-backdrop';
        backdrop.addEventListener('click', () => setNav(false));
        document.body.appendChild(backdrop);
      }
      const first = nav && nav.querySelector('a');
      if (first) first.focus({ preventScroll: true });
    } else if (backdrop) { backdrop.remove(); backdrop = null; }
  }
  if (burger) burger.addEventListener('click', () => setNav(!shell.classList.contains('nav-open')));
  if (nav) nav.addEventListener('click', (e) => { if (e.target.closest('a')) setNav(false); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && shell && shell.classList.contains('nav-open')) { setNav(false); if (burger) burger.focus(); }
  });
  // Leaving drawer mode (rotate / resize to desktop) must reset the state.
  (drawerMQ.addEventListener ? drawerMQ.addEventListener.bind(drawerMQ, 'change') : drawerMQ.addListener.bind(drawerMQ))(() => setNav(false));

  // ---- clickable table rows reachable by keyboard / remote ----------------
  function makeRowsFocusable(root) {
    root.querySelectorAll('tbody tr[onclick], tbody tr[data-id], tbody tr[data-lead]').forEach((tr) => {
      if (!tr.hasAttribute('tabindex')) tr.setAttribute('tabindex', '0');
    });
  }
  document.addEventListener('keydown', (e) => {
    if ((e.key === 'Enter' || e.key === ' ') && e.target.matches && e.target.matches('tbody tr[tabindex]')
        && !e.target.closest('a, button')) { e.preventDefault(); e.target.click(); }
  });
  const view = document.getElementById('view');
  if (view) {
    makeRowsFocusable(view);
    new MutationObserver(() => makeRowsFocusable(view)).observe(view, { childList: true, subtree: true });
  }

  // ---- TV / remote control -------------------------------------------------
  const ua = navigator.userAgent || '';
  const isTV = /SMART-TV|SmartTV|Tizen|Web0S|WebOS|NetCast|HbbTV|AFT[A-Z]|Android TV|GoogleTV|BRAVIA|VIDAA|Roku|CrKey/i.test(ua)
    || /[?&]tv=1/.test(location.search) || (function () { try { return localStorage.getItem('tv') === '1'; } catch (e) { return false; } })();
  if (!isTV) return;
  document.body.classList.add('tv');
  const FOCUSABLE = 'a[href], button:not(:disabled), input:not(:disabled):not([type=hidden]), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]';
  const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden'; };
  function move(dir) {
    const cur = document.activeElement && document.activeElement !== document.body ? document.activeElement : null;
    const items = [...document.querySelectorAll(FOCUSABLE)].filter(visible);
    if (!items.length) return;
    if (!cur) { items[0].focus(); return; }
    const c = cur.getBoundingClientRect(); const cx = c.left + c.width / 2, cy = c.top + c.height / 2;
    let best = null, bestScore = Infinity;
    for (const el of items) {
      if (el === cur) continue;
      const r = el.getBoundingClientRect(); const x = r.left + r.width / 2, y = r.top + r.height / 2;
      const dx = x - cx, dy = y - cy;
      const ok = dir === 'ArrowRight' ? dx > 4 : dir === 'ArrowLeft' ? dx < -4 : dir === 'ArrowDown' ? dy > 4 : dy < -4;
      if (!ok) continue;
      const along = dir === 'ArrowLeft' || dir === 'ArrowRight' ? Math.abs(dx) : Math.abs(dy);
      const across = dir === 'ArrowLeft' || dir === 'ArrowRight' ? Math.abs(dy) : Math.abs(dx);
      const score = along + across * 2.5;
      if (score < bestScore) { bestScore = score; best = el; }
    }
    if (best) { best.focus(); best.scrollIntoView({ block: 'nearest', inline: 'nearest' }); }
  }
  document.addEventListener('keydown', (e) => {
    if (!['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(e.key)) return;
    const t = e.target;
    // Let text fields and dropdowns keep their own arrow-key behaviour.
    if (t.matches && t.matches('textarea, select, input:not([type=button]):not([type=checkbox]):not([type=radio]):not([type=submit])')) return;
    e.preventDefault(); move(e.key);
  });
  // Remote "back" returns to the previous page / closes the drawer.
  document.addEventListener('keydown', (e) => {
    if (e.key === 'XF86Back' || e.keyCode === 10009 || e.keyCode === 461) { e.preventDefault(); history.back(); }
  });
})();
