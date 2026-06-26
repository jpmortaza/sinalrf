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
    { href: '/analista.html',  label: 'ANALISTA',     icon: '🕵️' },
    { href: '/rede.html',      label: 'CÂMERAS IP',   icon: '📷' },
    { href: '/3d.html',        label: '3D RF',        icon: '🔮' },
    { href: '/doppler.html',   label: 'DOPPLER',      icon: '🫀' },
    { href: '/health.html',    label: 'SAÚDE',        icon: '❤️'  },
    { href: '/intercept.html',  label: 'IMSI · INTCP', icon: '📱' },
    { href: '/emergencia.html', label: 'EMERGÊNCIA',   icon: '🚨' },
  ];

  /* ── Modo de HackRF por página (para o botão START) ──────────── */
  const MODOS = {
    '/':               'completo',
    '/index.html':     'completo',
    '/radio.html':     'radio',
    '/scanner.html':   'scanner',
    '/analista.html':  'tscm',
    '/rede.html':      'idle',
    '/3d.html':        'completo',
    '/doppler.html':   'doppler',
    '/health.html':    'completo',
    '/intercept.html': 'imsi',
    '/emergencia.html':'emergencia',
  };
  function modoAtual() {
    const p = location.pathname;
    if (p === '/' || p === '/index.html') return 'completo';
    for (const k in MODOS) { if (p.endsWith(k) && k !== '/') return MODOS[k]; }
    return 'completo';
  }

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
        <button class="srf-hk start" id="srfHkStart" title="Para o HackRF e inicia o processo desta página">▶ START</button>
        <button class="srf-hk stop"  id="srfHkStop"  title="Para tudo e libera o HackRF">■ STOP</button>
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

    // Botões START / STOP do HackRF
    const btnStart = document.getElementById('srfHkStart');
    const btnStop  = document.getElementById('srfHkStop');

    btnStart.addEventListener('click', async () => {
      const modo = modoAtual();
      btnStart.disabled = true;
      btnStart.textContent = '⏳ INICIANDO';
      try {
        const r = await fetch('/api/hackrf/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ modo }),
        });
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail || 'erro');
        btnStart.style.display = 'none';
        btnStart.classList.add('ativo');
        btnStop.style.display = 'inline-block';
        window.srfNav.setHrf(true);
      } catch (e) {
        alert('Erro ao iniciar HackRF: ' + e.message);
      } finally {
        btnStart.disabled = false;
        btnStart.textContent = '▶ START';
      }
    });

    btnStop.addEventListener('click', async () => {
      btnStop.disabled = true;
      btnStop.textContent = '⏳ PARANDO';
      try {
        await fetch('/api/hackrf/stop', { method: 'POST' });
      } catch (e) {}
      btnStop.style.display = 'none';
      btnStop.disabled = false;
      btnStop.textContent = '■ STOP';
      btnStart.style.display = 'inline-block';
      btnStart.classList.remove('ativo');
      window.srfNav.setHrf(false);
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
