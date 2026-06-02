/* mtzHRF — Nav unificada + gestão de tema
   Injeta #srf-nav em todas as páginas.
   Expõe: window.srfNav.setWs(bool), window.srfNav.setHrf(bool)
*/
(function () {
  'use strict';

  const PAGES = [
    { href: '/',               label: 'DASHBOARD',   icon: '⚡' },
    { href: '/radio.html',     label: 'RÁDIO FM',     icon: '📻' },
    { href: '/scanner.html',   label: 'SCANNER',      icon: '🌌' },
    { href: '/3d.html',        label: '3D RF',        icon: '🔮' },
    { href: '/doppler.html',   label: 'DOPPLER',      icon: '🫀' },
    { href: '/health.html',    label: 'SAÚDE',        icon: '❤️'  },
    { href: '/intercept.html', label: 'IMSI · INTCP', icon: '📱' },
  ];

  /* ── Detectar página ativa ───────────────────────────────────── */
  function isActive(href) {
    const p = location.pathname;
    if (href === '/') return p === '/' || p === '/index.html';
    return p === href || p.endsWith(href);
  }

  /* ── Tema ────────────────────────────────────────────────────── */
  const LS_KEY = 'srf-theme';
  function getTheme() { return localStorage.getItem(LS_KEY) || 'black'; }
  function setTheme(t) {
    localStorage.setItem(LS_KEY, t);
    document.documentElement.setAttribute('data-theme', t === 'neutral' ? 'neutral' : '');
    const btn = document.getElementById('srfThemeBtn');
    if (btn) btn.title = t === 'neutral' ? 'Mudar para tema preto' : 'Mudar para tema neutro';
    if (btn) btn.textContent = t === 'neutral' ? '◑' : '◐';
  }

  /* ── Build nav HTML ──────────────────────────────────────────── */
  function buildNav() {
    const links = PAGES.map(p => {
      const active = isActive(p.href) ? ' active' : '';
      return `<a class="srf-link${active}" href="${p.href}"><span class="srf-icon">${p.icon}</span>${p.label}</a>`;
    }).join('');

    const t = getTheme();
    const icon = t === 'neutral' ? '◑' : '◐';
    const title = t === 'neutral' ? 'Mudar para tema preto' : 'Mudar para tema neutro';

    return `
      <a class="srf-logo" href="/">mtz<span>HRF</span></a>
      <nav class="srf-links">${links}</nav>
      <div class="srf-right">
        <div class="srf-ws">
          <span class="srf-ws-dot" id="srfWsDot"></span>
          <span id="srfWsLbl">OFF</span>
        </div>
        <span class="srf-hrf" id="srfHrfBadge">HRF —</span>
        <button class="srf-theme" id="srfThemeBtn" title="${title}">${icon}</button>
      </div>`;
  }

  /* ── Inject ──────────────────────────────────────────────────── */
  function inject() {
    // Aplica o tema salvo antes de renderizar (evita flash)
    const saved = getTheme();
    if (saved === 'neutral') {
      document.documentElement.setAttribute('data-theme', 'neutral');
    }

    // Cria o elemento nav se não existir
    let nav = document.getElementById('srf-nav');
    if (!nav) {
      nav = document.createElement('div');
      nav.id = 'srf-nav';
      document.body.insertBefore(nav, document.body.firstChild);
    }
    nav.innerHTML = buildNav();

    // Toggle de tema
    document.getElementById('srfThemeBtn').addEventListener('click', () => {
      setTheme(getTheme() === 'neutral' ? 'black' : 'neutral');
    });
  }

  /* ── API pública ─────────────────────────────────────────────── */
  window.srfNav = {
    setWs(on) {
      const dot = document.getElementById('srfWsDot');
      const lbl = document.getElementById('srfWsLbl');
      if (dot) dot.className = 'srf-ws-dot' + (on ? ' on' : '');
      if (lbl) lbl.textContent = on ? 'WS' : 'OFF';
    },
    setHrf(on) {
      const b = document.getElementById('srfHrfBadge');
      if (!b) return;
      b.textContent = on ? 'HRF: ATIVO' : 'HRF —';
      b.className   = 'srf-hrf' + (on ? ' on' : '');
    },
  };

  /* ── Executa após DOM pronto ─────────────────────────────────── */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', inject);
  } else {
    inject();
  }
})();
