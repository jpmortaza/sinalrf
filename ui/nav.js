/* mtzRF — Nav unificada + gestão de tema
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
    { href: '/sentinela.html', label: 'SENTINELA',    icon: '🛡️' },
    { href: '/burst.html',     label: 'BURST',        icon: '💥' },
    { href: '/sonda.html',     label: 'SONDA',        icon: '🔎' },
    { href: '/adsb.html',      label: 'AVIÕES',       icon: '✈️' },
    { href: '/ism.html',       label: 'ISM/IoT',      icon: '📟' },
    { href: '/rede.html',      label: 'CÂMERAS IP',   icon: '📷' },
    { href: '/wifi.html',      label: 'WIFI · RT',    icon: '🛜' },
    { href: '/historico.html', label: 'HISTÓRICO',    icon: '🗂' },
    { href: '/alertas.html',   label: 'ALERTAS',      icon: '🔔' },
    { href: '/gps.html',       label: 'GPS',          icon: '🛰️' },
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
    '/sentinela.html': 'scanner',
    '/burst.html':     'tscm',
    '/sonda.html':     'tscm',
    '/adsb.html':      'idle',
    '/ism.html':       'tscm',
    '/rede.html':      'idle',
    '/wifi.html':      'idle',
    '/historico.html': 'idle',
    '/alertas.html':   'idle',
    '/gps.html':       'gps',
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
  function getTheme() {
    // páginas de console (data-force-dark) sempre escuras
    if (document.documentElement.hasAttribute('data-force-dark')) return 'black';
    return localStorage.getItem(LS_KEY) || 'neutral';
  }
  function setTheme(t) {
    localStorage.setItem(LS_KEY, t);
    document.documentElement.setAttribute('data-theme', t === 'neutral' ? 'neutral' : '');
    const btn = document.getElementById('srfThemeBtn');
    if (btn) btn.title = t === 'neutral' ? 'Mudar para tema escuro' : 'Mudar para tema claro';
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
    const title = t === 'neutral' ? 'Mudar para tema escuro' : 'Mudar para tema claro';

    return `
      <a class="srf-logo" href="/">mtz<span>RF</span></a>
      <nav class="srf-links">${links}</nav>
      <div class="srf-right">
        <button class="srf-hk start" id="srfHkStart" title="Para o HackRF e inicia o processo desta página">▶ START</button>
        <button class="srf-hk stop"  id="srfHkStop"  title="Para tudo e libera o HackRF">■ STOP</button>
        <div class="srf-ws">
          <span class="srf-ws-dot" id="srfWsDot"></span>
          <span id="srfWsLbl">OFF</span>
        </div>
        <span class="srf-hrf" id="srfHrfBadge">RF —</span>
        <button class="srf-theme" id="srfFsBtn" title="Tela cheia / janela">⛶</button>
        <button class="srf-theme" id="srfThemeBtn" title="${title}">${icon}</button>
      </div>`;
  }

  /* ── Inject ──────────────────────────────────────────────────── */
  function inject() {
    // Ícone da janela/aba (mtzRF) em todas as páginas
    if (!document.querySelector('link[rel="icon"]')) {
      const ic = document.createElement('link');
      ic.rel = 'icon'; ic.type = 'image/svg+xml'; ic.href = '/favicon.svg';
      document.head.appendChild(ic);
    }

    // Aplica o tema salvo antes de renderizar (evita flash)
    const forceDark = document.documentElement.hasAttribute('data-force-dark');
    const saved = getTheme();
    if (forceDark) {
      document.documentElement.removeAttribute('data-theme');   // console sempre escuro
    } else if (saved === 'neutral') {
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

    // Toggle de tema (oculto em páginas de console sempre-escuras)
    const themeBtn = document.getElementById('srfThemeBtn');
    if (forceDark) {
      themeBtn.style.display = 'none';
    } else {
      themeBtn.addEventListener('click', () => {
        setTheme(getTheme() === 'neutral' ? 'black' : 'neutral');
      });
    }

    // Toggle de tela cheia (entra/sai do fullscreen imersivo)
    const fsBtn = document.getElementById('srfFsBtn');
    if (fsBtn) {
      fsBtn.addEventListener('click', () => {
        if (document.fullscreenElement) {
          document.exitFullscreen().catch(() => {});
        } else {
          document.documentElement.requestFullscreen().catch(() => {});
        }
      });
      document.addEventListener('fullscreenchange', () => {
        fsBtn.textContent = document.fullscreenElement ? '🗗' : '⛶';
        fsBtn.title = document.fullscreenElement ? 'Sair da tela cheia' : 'Tela cheia';
      });
    }

    // Contador de alertas no menu (poll leve em todas as páginas)
    const alLink = document.querySelector('.srf-link[href="/alertas.html"]');
    if (alLink) {
      const atualizaAlertas = async () => {
        try {
          const d = await (await fetch('/api/alertas')).json();
          const n = (d.alertas || []).length;
          const altos = (d.alertas || []).filter(a => a.nivel >= 2).length;
          let b = alLink.querySelector('.srf-albadge');
          if (n > 0) {
            if (!b) { b = document.createElement('span'); b.className = 'srf-albadge'; alLink.appendChild(b); }
            b.textContent = ' ' + n;
            b.style.cssText = 'margin-left:4px;padding:0 5px;border-radius:8px;font-size:9px;font-weight:700;background:' +
              (altos ? 'var(--r)' : 'var(--g3)') + ';color:#fff;';
          } else if (b) { b.remove(); }
        } catch {}
      };
      atualizaAlertas();
      setInterval(atualizaAlertas, 15000);
    }

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
      b.textContent = on ? 'RF: ATIVO' : 'RF —';
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
