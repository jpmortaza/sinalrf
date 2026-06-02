// ════════════════════════════════════════════════════════════════════
//  mtzHRF — app.js v2.0
//  WiFi RSSI · HackRF Espectro Wideband · Doppler Corporal ·
//  Radar Acústico · FM Monitor · Ghost Signals
// ════════════════════════════════════════════════════════════════════

let frame      = null;
let tUlt       = performance.now();
let fps        = 0;
let blipAngle  = 0;

// Buffers cliente para Doppler time-series
const _dopplerHist = [];
const DOPPLER_MAX  = 120;

// ── WebSocket ─────────────────────────────────────────────────────
function conectar() {
  const ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen  = () => { if (window.srfNav) window.srfNav.setWs(true); };
  ws.onclose = () => {
    if (window.srfNav) window.srfNav.setWs(false);
    setTimeout(conectar, 2000);
  };
  ws.onmessage = e => {
    try {
      frame = JSON.parse(e.data);
      const agora = performance.now();
      fps = Math.round(1000 / (agora - tUlt));
      tUlt = agora;
      atualizarHUD(frame);
    } catch (_) {}
  };
}
conectar();

// ════════════════════════════════════════════════════════════════════
//  HUD principal
// ════════════════════════════════════════════════════════════════════
function atualizarHUD(f) {
  // Header
  const bFonte = document.getElementById('badgeFonte');
  if (bFonte) {
    bFonte.textContent = (f.fonte || 'SIM').toUpperCase();
    bFonte.classList.toggle('active', f.fonte === 'real');
  }
  $('fpsLabel').textContent  = `${fps} fps`;
  $('footerFps').textContent = `${fps} fps`;
  $('frameValor').textContent = f.frame || 0;

  const a = f.audio || {};
  const isAirpods = a.is_airpods || (a.dispositivo || '').toLowerCase().includes('airpod');
  const bAudio = $('badgeAudio');
  if (bAudio) {
    bAudio.textContent = isAirpods ? 'AIRPODS PRO' : (a.dispositivo || 'MIC').toUpperCase().slice(0,16);
    bAudio.classList.toggle('active', isAirpods);
  }

  // Badge HackRF
  const hrf = f.hackrf || {};
  const bHrf = $('badgeHackrf');
  if (bHrf) {
    bHrf.textContent = hrf.conectado ? 'HRF: ATIVO' : 'HRF: OFFLINE';
    bHrf.classList.toggle('active', !!hrf.conectado);
  }
  if (window.srfNav) window.srfNav.setHrf(!!hrf.conectado);

  // Badge Ghost
  const esp = f.espectro || {};
  const nGhost = (esp.anomalos || []).length;
  const bGhost = $('badgeGhost');
  if (bGhost) {
    bGhost.textContent = `GHOST: ${nGhost}`;
    bGhost.classList.toggle('active', nGhost > 0);
  }

  // Espectro
  atualizarEspectro(esp);

  // RSSI
  $('rssiValor').textContent = f.rssi != null ? `${f.rssi} dBm` : '—';
  $('varValor').textContent  = f.variancia != null ? f.variancia.toFixed(3) : '—';
  $('wifiVar2').textContent  = f.variancia != null ? f.variancia.toFixed(3) : '—';

  const thr    = f.threshold || 0.18;
  const maxVar = 4.0;
  const varPct = Math.min(100, (f.variancia / maxVar) * 100);
  const thrPct = Math.min(100, (thr / maxVar) * 100);

  const vFill = $('varFill');
  if (vFill) {
    vFill.style.width = `${varPct}%`;
    vFill.className   = 'var-fill' + (varPct > 80 ? ' max' : varPct > 40 ? ' alto' : '');
  }
  $('varThr').style.left    = `${thrPct}%`;
  $('thrLabel').textContent = `▲ THR=${thr}`;
  $('varPct').textContent   = `${Math.round(varPct)}%`;

  // Presença WiFi
  const p   = f.presenca || {};
  const det = p.detectado;
  const mov = p.atividade === 'se movendo' || p.atividade === 'muito ativo';
  const dot = $('statusDot');
  if (dot) dot.className = 's-dot' + (det ? (mov ? ' mov' : ' on') : '');
  const sTxt = $('statusTxt');
  if (sTxt) {
    const lbl = det ? (p.atividade === 'muito ativo' ? 'MUITO ATIVO' :
                       p.atividade === 'se movendo'  ? 'SE MOVENDO' : 'PARADO') : 'AUSENTE';
    sTxt.innerHTML  = lbl + (det ? '' : '<span class="cursor"></span>');
    sTxt.className  = 's-txt' + (det ? (mov ? ' mov' : ' det') : '');
  }
  $('atividadeLabel').textContent = p.atividade || '—';
  $('presencaConf').textContent   = `${Math.round((p.confianca || 0) * 100)}%`;
  $('presencaBarra').style.width  = `${Math.round((p.confianca || 0) * 100)}%`;

  // Respiração
  const resp = f.respiracao || {};
  $('respBpm').textContent  = resp.bpm > 0 ? resp.bpm.toFixed(1) : '—';
  $('respConf').textContent = `${Math.round((resp.confianca || 0) * 100)}%`;
  $('respBarra').style.width= `${Math.min(100, Math.round((resp.confianca || 0) * 100))}%`;
  const rfEl = $('respFonte');
  rfEl.textContent = resp.fonte || '—';
  rfEl.className   = 'ftag' + (resp.fonte === 'wifi' ? ' wifi' : '') +
                               (resp.fonte === 'audio' ? ' audio' : '') +
                               (resp.fonte === 'hackrf' ? ' hackrf' : '');
  $('respFonteFinal').textContent = resp.fonte || '—';

  const ra = a.resp_audio || {};
  $('respAudioTag').textContent  = ra.bpm > 0 ? `${ra.bpm} rpm (${Math.round((ra.confianca||0)*100)}%)` : '—';

  // Batimentos
  const card = f.batimentos || {};
  $('cardBpm').textContent  = card.bpm > 0 ? card.bpm.toFixed(0) : '—';
  $('cardConf').textContent = `${Math.round((card.confianca || 0) * 100)}%`;
  $('cardBarra').style.width= `${Math.min(100, Math.round((card.confianca || 0) * 100))}%`;
  const cfEl = $('cardFonte');
  cfEl.textContent = card.fonte || '—';
  cfEl.className   = 'ftag' + (card.fonte === 'wifi'  ? ' wifi'  : '') +
                               (card.fonte === 'audio' ? ' audio' : '');

  const ca = a.card_audio || {};
  $('cardAudioTag').textContent  = ca.bpm > 0 ? `${ca.bpm} bpm (${Math.round((ca.confianca||0)*100)}%)` : '—';

  // Microfone
  $('devNome').textContent = a.dispositivo || '—';
  $('devDb').textContent   = a.amplitude_db != null ? `${a.amplitude_db} dB` : '—';
  const rat2 = $('respAudioTag2'); if (rat2) rat2.textContent = ra.bpm > 0 ? `${ra.bpm} rpm (${Math.round((ra.confianca||0)*100)}%)` : '—';
  const cat2 = $('cardAudioTag2'); if (cat2) cat2.textContent = ca.bpm > 0 ? `${ca.bpm} bpm (${Math.round((ca.confianca||0)*100)}%)` : '—';

  // Doppler corporal
  atualizarDoppler(hrf.doppler || {});

  // HackRF canais
  atualizarHackRF(hrf);
}

// ════════════════════════════════════════════════════════════════════
//  Radio Player — Web Audio API + WebSocket streaming
// ════════════════════════════════════════════════════════════════════
let _radioWs      = null;
let _radioCtx     = null;
let _radioQueue   = [];
let _radioPlaying = false;
let _radioFreq    = null;
let _radioNome    = '';
let _radioNextAt  = 0;   // AudioContext.currentTime do próximo chunk
let _rpBarsAnim   = null;

function radioTocar(freq, nome) {
  if (_radioWs) radioParar();

  _radioFreq = freq;
  _radioNome = nome || `${freq} MHz`;

  // Mostra player
  const pl = $('radioPlayer');
  $('rpFreq').textContent  = `${freq} MHz`;
  $('rpNome').textContent  = nome || '';
  $('rpStatus').innerHTML  = 'conectando<span class="cursor"></span>';
  pl.classList.remove('oculto');

  // AudioContext (precisa de gesto do usuário — clique já satisfaz)
  if (!_radioCtx) _radioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 48000 });
  _radioCtx.resume();
  _radioNextAt  = _radioCtx.currentTime + 0.3; // 300ms buffer inicial
  _radioQueue   = [];
  _radioPlaying = false;

  _radioWs = new WebSocket(`ws://${location.host}/ws/radio?freq=${freq}`);
  _radioWs.binaryType = 'arraybuffer';

  _radioWs.onopen = () => {
    $('rpStatus').innerHTML = 'recebendo áudio<span class="cursor"></span>';
  };

  _radioWs.onmessage = (e) => {
    if (typeof e.data === 'string') {
      const msg = JSON.parse(e.data);
      if (msg.erro) { $('rpStatus').textContent = `ERRO: ${msg.erro}`; radioParar(); }
      return;
    }
    // Chunk PCM int16
    const int16 = new Int16Array(e.data);
    const float32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / 32768;

    // Cria AudioBuffer e agenda
    const buf = _radioCtx.createBuffer(1, float32.length, 48000);
    buf.copyToChannel(float32, 0);

    const src = _radioCtx.createBufferSource();
    src.buffer = buf;
    src.connect(_radioCtx.destination);

    const agora = _radioCtx.currentTime;
    if (_radioNextAt < agora + 0.05) _radioNextAt = agora + 0.05; // anti-gap
    src.start(_radioNextAt);
    _radioNextAt += buf.duration;

    $('rpStatus').textContent = 'ao vivo';
    _animarBarras();
  };

  _radioWs.onclose = () => {
    if ($('rpStatus').textContent === 'ao vivo' || $('rpStatus').textContent.startsWith('reconect')) return;
    $('rpStatus').textContent = 'desconectado';
    setTimeout(() => { if (_radioFreq === freq) $('radioPlayer').classList.add('oculto'); }, 2000);
  };

  _radioWs.onerror = () => { $('rpStatus').textContent = 'erro de conexão'; };
}

function radioParar() {
  if (_radioWs) { _radioWs.send('STOP'); _radioWs.close(); _radioWs = null; }
  _radioFreq = null;
  $('radioPlayer').classList.add('oculto');
  if (_rpBarsAnim) { cancelAnimationFrame(_rpBarsAnim); _rpBarsAnim = null; }
}

function _animarBarras() {
  const bars = document.querySelectorAll('.rp-bar');
  bars.forEach(b => { b.style.height = (4 + Math.random() * 16) + 'px'; });
  _rpBarsAnim = requestAnimationFrame(_animarBarras);
}

// ════════════════════════════════════════════════════════════════════
//  Espectro — waterfall + linha + FM + ghost
// ════════════════════════════════════════════════════════════════════
function atualizarEspectro(esp) {
  const status = $('espectroStatus');
  if (status) {
    if (!esp.disponivel) { status.textContent = '[SEM TOOLS]'; return; }
    if (!esp.conectado)  { status.textContent = '[HRF OFFLINE]'; return; }
    status.textContent = `[${esp.n_pontos || 0} pts · baseline ${esp.baseline_ok ? 'OK' : '...'}]`;
  }

  // FM chips — clicáveis para ouvir
  const fmEl = $('fmGrid');
  const fm = esp.fm || [];
  if (fm.length > 0) {
    fmEl.innerHTML = fm.map(s => {
      const ativo = _radioFreq === s.freq_mhz;
      return `<div class="fm-chip" style="cursor:pointer;${ativo ? 'border-color:var(--g2);background:rgba(71,223,127,0.07)' : ''}"
        onclick="radioTocar(${s.freq_mhz}, '${(s.nome||'').replace(/'/g,"\\'")}')">
        <span class="fm-freq">${s.freq_mhz} ${ativo ? '▶' : ''}</span>
        <span class="fm-nome">${s.nome || 'sem nome'}</span>
        <span class="fm-dbm">${s.dbm} dBm</span>
      </div>`;
    }).join('');
  } else if (esp.conectado) {
    fmEl.innerHTML = '<span style="font-size:10px;color:var(--g3)">escaneando…<span class="cursor"></span></span>';
  }

  // Ghost signals
  const ghostEl = $('ghostList');
  const ghosts  = esp.anomalos || [];
  const bGhost  = $('badgeGhost');
  if (bGhost) { bGhost.textContent = `GHOST: ${ghosts.length}`; bGhost.classList.toggle('active', ghosts.length > 0); }

  if (ghosts.length > 0) {
    ghostEl.innerHTML = ghosts.map(g => {
      const pct = Math.min(100, (g.delta_db / 30) * 100);
      return `<div class="ghost-item" style="cursor:pointer" onclick="radioTocar(${g.freq_mhz}, 'GHOST ${g.freq_mhz}MHz')" title="Clique para ouvir">
        <span class="ghost-freq">${g.freq_mhz} MHz</span>
        <span class="ghost-delta">+${g.delta_db} dB</span>
        <div class="ghost-bar-wrap"><div class="ghost-bar" style="width:${pct}%"></div></div>
        <span style="font-size:9px;color:var(--g3);width:52px">${g.dbm} dBm</span>
        <span style="font-size:9px;color:var(--g3)">▶</span>
      </div>`;
    }).join('');
  } else {
    ghostEl.innerHTML = '<div class="ghost-empty">nenhuma anomalia detectada<span class="cursor"></span></div>';
  }
}

// ── Doppler ────────────────────────────────────────────────────────
function atualizarDoppler(dop) {
  const pres = dop.presente;
  const dot  = $('dopplerDot');
  if (dot) dot.className = 's-dot' + (pres ? ' on' : '');
  const txt = $('dopplerTxt');
  if (txt) {
    txt.innerHTML = pres ? 'PRESENTE' : 'AUSENTE<span class="cursor"></span>';
    txt.className = 's-txt' + (pres ? ' det' : '');
  }
  $('dopplerBpm').textContent  = dop.resp_bpm > 0 ? dop.resp_bpm.toFixed(1) : '—';
  $('dopplerBarra').style.width= `${Math.min(100, Math.round((dop.confianca || 0) * 100))}%`;
  $('dopplerVar').textContent  = dop.variancia != null ? dop.variancia.toFixed(3) : '—';
  $('dopplerConf').textContent = dop.confianca != null ? `${Math.round(dop.confianca * 100)}%` : '—';
  $('dopplerN').textContent    = dop.n_amostras || '—';

  // Buffer histórico
  if (dop.variancia != null) {
    _dopplerHist.push(dop.variancia);
    if (_dopplerHist.length > DOPPLER_MAX) _dopplerHist.shift();
  }
}

// ── HackRF canais ─────────────────────────────────────────────────
function atualizarHackRF(hrf) {
  const bodyEl = $('hackrfBody');
  if (!bodyEl) return;

  if (!hrf.disponivel) {
    bodyEl.innerHTML = `<div class="hrf-off"><span style="color:var(--y2)">&gt;</span>
      <span>hackrf_info não encontrado — brew install hackrf</span></div>`;
    return;
  }
  if (!hrf.conectado) {
    bodyEl.innerHTML = `<div class="hrf-off"><span style="color:var(--y2)">&gt;</span>
      <span>HackRF não detectado — conecte via USB</span></div>`;
    return;
  }

  const canais = hrf.canais || {};
  const FREQS  = { 1: '2412 MHz', 6: '2437 MHz', 11: '2462 MHz' };
  const DB_MIN = -90, DB_MAX = -20;
  let html = '<div class="hrf-grid">';
  for (const [canal, info] of Object.entries(canais)) {
    const pct = Math.max(0, Math.min(100, ((info.potencia_dbm - DB_MIN) / (DB_MAX - DB_MIN)) * 100));
    const varTxt = info.variancia > 0.5 ? `var:${info.variancia.toFixed(2)}` : '';
    html += `<div class="hrf-row">
      <span class="hrf-ch">CH ${canal}</span>
      <span class="hrf-freq">${FREQS[canal] || ''}</span>
      <div class="hrf-track"><div class="hrf-fill" style="width:${pct}%"></div></div>
      <span class="hrf-dbm">${info.potencia_dbm} dBm</span>
      <span class="hrf-var">${varTxt}</span>
    </div>`;
  }
  if (Object.keys(canais).length === 0) {
    html += `<div class="hrf-off"><span style="color:var(--y2)">></span>
      <span>primeira varredura<span class="cursor"></span></span></div>`;
  }
  html += '</div>';
  bodyEl.innerHTML = html;
}

// ════════════════════════════════════════════════════════════════════
//  CANVAS — Espectro linha
// ════════════════════════════════════════════════════════════════════
const cSpec = document.getElementById('spectrumCanvas');
const xSpec = cSpec.getContext('2d');

function desenharEspectroLinha() {
  const w = cSpec.width, h = cSpec.height;
  xSpec.clearRect(0, 0, w, h);
  const esp = frame?.espectro;
  if (!esp?.dbs?.length || esp.dbs.length < 2) {
    xSpec.strokeStyle = 'rgba(71,223,127,0.10)'; xSpec.lineWidth = 1;
    xSpec.setLineDash([4, 6]);
    xSpec.beginPath(); xSpec.moveTo(0, h/2); xSpec.lineTo(w, h/2); xSpec.stroke();
    xSpec.setLineDash([]);
    return;
  }

  const dbs = esp.dbs;
  const mn  = Math.min(...dbs) - 2;
  const mx  = Math.max(...dbs) + 2;
  const px  = i => (i / (dbs.length - 1)) * w;
  const py  = v => h - 4 - ((v - mn) / (mx - mn)) * (h - 8);

  // Área preenchida
  xSpec.beginPath();
  dbs.forEach((v, i) => i === 0 ? xSpec.moveTo(px(i), py(v)) : xSpec.lineTo(px(i), py(v)));
  xSpec.lineTo(w, h); xSpec.lineTo(0, h); xSpec.closePath();
  const agr = xSpec.createLinearGradient(0, 0, 0, h);
  agr.addColorStop(0, 'rgba(71,223,127,0.18)');
  agr.addColorStop(1, 'rgba(71,223,127,0.01)');
  xSpec.fillStyle = agr; xSpec.fill();

  // Linha
  xSpec.beginPath();
  dbs.forEach((v, i) => i === 0 ? xSpec.moveTo(px(i), py(v)) : xSpec.lineTo(px(i), py(v)));
  xSpec.strokeStyle = '#47DF7F'; xSpec.lineWidth = 1.4;
  xSpec.shadowColor = '#47DF7F'; xSpec.shadowBlur = 4;
  xSpec.stroke(); xSpec.shadowBlur = 0;

  // Marcadores de picos FM
  const fm = frame?.espectro?.fm || [];
  const freqs = esp.freqs || [];
  if (freqs.length > 0 && fm.length > 0) {
    const fMin = freqs[0], fMax = freqs[freqs.length - 1];
    fm.forEach(s => {
      const xp = ((s.freq_mhz - fMin) / (fMax - fMin)) * w;
      if (xp < 0 || xp > w) return;
      xSpec.beginPath(); xSpec.moveTo(xp, h); xSpec.lineTo(xp, h * 0.3);
      xSpec.strokeStyle = 'rgba(245,216,0,0.5)'; xSpec.lineWidth = 1;
      xSpec.setLineDash([2, 4]); xSpec.stroke(); xSpec.setLineDash([]);
      xSpec.fillStyle = '#F5D800'; xSpec.font = '8px "JetBrains Mono",monospace';
      xSpec.fillText(`${s.freq_mhz}`, xp + 2, h * 0.3 - 2);
    });
  }
}

// ════════════════════════════════════════════════════════════════════
//  CANVAS — Waterfall (espectro ao longo do tempo)
// ════════════════════════════════════════════════════════════════════
const cWF  = document.getElementById('waterfallCanvas');
const xWF  = cWF.getContext('2d');
let wfImageData = null;

function dbToColor(db) {
  // -100 → preto, -70 → verde escuro, -50 → verde, -35 → amarelo, > -20 → branco
  const t = Math.max(0, Math.min(1, (db + 100) / 80));
  if (t < 0.3)  { const k = t / 0.3; return [Math.round(k * 10), Math.round(k * 40), Math.round(k * 10)]; }
  if (t < 0.55) { const k = (t - 0.3) / 0.25; return [Math.round(10 + k*61), Math.round(40 + k*183), Math.round(10 + k*117)]; }
  if (t < 0.75) { const k = (t - 0.55) / 0.20; return [Math.round(71 + k*174), Math.round(223 + k*(216-223)), Math.round(127 - k*127)]; }
  { const k = (t - 0.75) / 0.25; return [Math.round(245 + k*10), Math.round(216 + k*39), Math.round(k * 255)]; }
}

function desenharWaterfall() {
  const w = cWF.width, h = cWF.height;
  const hist = frame?.espectro?.historico;
  if (!hist || hist.length === 0) {
    xWF.fillStyle = '#000'; xWF.fillRect(0, 0, w, h);
    xWF.fillStyle = 'rgba(71,223,127,0.12)'; xWF.font = '11px "JetBrains Mono",monospace';
    xWF.fillText('aguardando HackRF…', w/2 - 80, h/2);
    return;
  }

  const rowH = Math.max(1, Math.floor(h / hist.length));
  const nCols = hist[0]?.length || 1;

  // Desenha do mais antigo (topo) para o mais novo (base)
  xWF.fillStyle = '#000'; xWF.fillRect(0, 0, w, h);

  hist.forEach((row, ri) => {
    const y = Math.floor(ri * (h / hist.length));
    const rh = Math.ceil(h / hist.length) + 1;
    for (let ci = 0; ci < row.length; ci++) {
      const x  = Math.floor((ci / nCols) * w);
      const x2 = Math.floor(((ci + 1) / nCols) * w);
      const [r, g, b] = dbToColor(row[ci]);
      xWF.fillStyle = `rgb(${r},${g},${b})`;
      xWF.fillRect(x, y, x2 - x + 1, rh);
    }
  });

  // Linha mais recente com brilho
  const last = hist[hist.length - 1];
  const y = h - rowH;
  for (let ci = 0; ci < last.length; ci++) {
    const x  = Math.floor((ci / nCols) * w);
    const x2 = Math.floor(((ci + 1) / nCols) * w);
    const [r, g, b] = dbToColor(last[ci] + 4); // ligeiramente mais brilhante
    xWF.fillStyle = `rgb(${Math.min(255,r+30)},${Math.min(255,g+30)},${Math.min(255,b+30)})`;
    xWF.fillRect(x, y, x2 - x + 1, rowH + 1);
  }
}

// ════════════════════════════════════════════════════════════════════
//  CANVAS — Doppler time-series
// ════════════════════════════════════════════════════════════════════
const cDop = document.getElementById('dopplerCanvas');
const xDop = cDop ? cDop.getContext('2d') : null;

function desenharDoppler() {
  if (!xDop) return;
  const w = cDop.width, h = cDop.height;
  xDop.clearRect(0, 0, w, h);
  if (_dopplerHist.length < 2) {
    xDop.strokeStyle = 'rgba(71,223,127,0.10)'; xDop.lineWidth = 1;
    xDop.setLineDash([4, 6]);
    xDop.beginPath(); xDop.moveTo(0, h/2); xDop.lineTo(w, h/2); xDop.stroke();
    xDop.setLineDash([]);
    return;
  }
  const mn = 0;
  const mx = Math.max(Math.max(..._dopplerHist) * 1.2, 3);
  const px = i => (i / (_dopplerHist.length - 1)) * w;
  const py = v => h - 4 - ((v - mn) / (mx - mn)) * (h - 8);

  xDop.beginPath();
  _dopplerHist.forEach((v, i) => i === 0 ? xDop.moveTo(px(i), py(v)) : xDop.lineTo(px(i), py(v)));
  xDop.lineTo(w, h); xDop.lineTo(0, h); xDop.closePath();
  const agr = xDop.createLinearGradient(0, 0, 0, h);
  agr.addColorStop(0, 'rgba(71,223,127,0.20)');
  agr.addColorStop(1, 'rgba(71,223,127,0.01)');
  xDop.fillStyle = agr; xDop.fill();

  xDop.beginPath();
  _dopplerHist.forEach((v, i) => i === 0 ? xDop.moveTo(px(i), py(v)) : xDop.lineTo(px(i), py(v)));
  xDop.strokeStyle = '#47DF7F'; xDop.lineWidth = 1.8;
  xDop.shadowColor = '#47DF7F'; xDop.shadowBlur = 6;
  xDop.stroke(); xDop.shadowBlur = 0;

  // Linha de threshold de presença
  const thrY = py(1.5);
  xDop.strokeStyle = 'rgba(245,216,0,0.4)'; xDop.lineWidth = 1;
  xDop.setLineDash([3, 4]);
  xDop.beginPath(); xDop.moveTo(0, thrY); xDop.lineTo(w, thrY); xDop.stroke();
  xDop.setLineDash([]);
  xDop.fillStyle = 'rgba(245,216,0,0.5)'; xDop.font = '8px "JetBrains Mono",monospace';
  xDop.fillText('THR', 4, thrY - 2);
}

// ════════════════════════════════════════════════════════════════════
//  CANVAS — RSSI history
// ════════════════════════════════════════════════════════════════════
const cRSSI = document.getElementById('rssiCanvas');
const xRSSI = cRSSI.getContext('2d');

function desenharRSSI() {
  const w = cRSSI.width, h = cRSSI.height;
  xRSSI.clearRect(0, 0, w, h);
  const hist = frame?.historico;
  if (!hist || hist.length < 2) {
    xRSSI.strokeStyle = 'rgba(71,223,127,0.12)'; xRSSI.lineWidth = 1;
    xRSSI.setLineDash([4, 6]);
    xRSSI.beginPath(); xRSSI.moveTo(0, h/2); xRSSI.lineTo(w, h/2); xRSSI.stroke();
    xRSSI.setLineDash([]);
    return;
  }
  const mn = Math.min(...hist) - 1, mx = Math.max(...hist) + 1;
  const px = i => (i / (hist.length - 1)) * w;
  const py = v => h - ((v - mn) / (mx - mn)) * (h - 12) - 6;

  xRSSI.beginPath();
  hist.forEach((v, i) => i === 0 ? xRSSI.moveTo(px(i), py(v)) : xRSSI.lineTo(px(i), py(v)));
  xRSSI.lineTo(w, h); xRSSI.lineTo(0, h); xRSSI.closePath();
  const grad = xRSSI.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, 'rgba(71,223,127,0.22)');
  grad.addColorStop(1, 'rgba(71,223,127,0.01)');
  xRSSI.fillStyle = grad; xRSSI.fill();

  xRSSI.beginPath();
  hist.forEach((v, i) => i === 0 ? xRSSI.moveTo(px(i), py(v)) : xRSSI.lineTo(px(i), py(v)));
  xRSSI.strokeStyle = '#47DF7F'; xRSSI.lineWidth = 1.8;
  xRSSI.shadowColor = '#47DF7F'; xRSSI.shadowBlur = 6;
  xRSSI.stroke(); xRSSI.shadowBlur = 0;
}

// ════════════════════════════════════════════════════════════════════
//  CANVAS — Presença SVG sweep
// ════════════════════════════════════════════════════════════════════
function animarPresenca() {
  blipAngle += 0.03;
  const det  = frame?.presenca?.detectado;
  const conf = frame?.presenca?.confianca || 0;
  const cx = 65, cy = 65;
  const sweep = document.getElementById('radarSweep');
  if (sweep) {
    const r = 55, a0 = blipAngle, a1 = a0 + 0.5;
    const x0 = cx + r * Math.sin(a0), y0 = cy - r * Math.cos(a0);
    const x1 = cx + r * Math.sin(a1), y1 = cy - r * Math.cos(a1);
    sweep.setAttribute('d', `M${cx},${cy} L${x0},${y0} A${r},${r} 0 0,1 ${x1},${y1} Z`);
  }
  [{ id:'blip0', r:38, da:0.8 }, { id:'blip1', r:55, da:2.1 }].forEach(b => {
    const el = document.getElementById(b.id); if (!el) return;
    if (det) {
      el.setAttribute('cx', cx + b.r * Math.sin(blipAngle + b.da));
      el.setAttribute('cy', cy - b.r * Math.cos(blipAngle + b.da));
      el.style.opacity = Math.max(0, Math.sin(blipAngle * 2 + b.da)) * conf;
    } else { el.style.opacity = 0; }
  });
}

// ════════════════════════════════════════════════════════════════════
//  CANVAS — Respiração
// ════════════════════════════════════════════════════════════════════
const cResp = document.getElementById('respCanvas');
const xResp = cResp.getContext('2d');
let tResp = 0;

function animarResp() {
  const w = cResp.width, h = cResp.height;
  xResp.clearRect(0, 0, w, h);
  const bpm = frame?.respiracao?.bpm || 0;
  const conf= frame?.respiracao?.confianca || 0;
  if (bpm <= 0) { desenharPlano(xResp, w, h); return; }
  tResp += 0.016 * (bpm / 60) * 2;
  const amp = (h / 2 - 5) * Math.min(1, conf * 2 + 0.25);
  xResp.beginPath();
  for (let x = 0; x <= w; x++) {
    const t = tResp + (x / w) * 2;
    const y = h/2 - amp * Math.sin(2 * Math.PI * t);
    x === 0 ? xResp.moveTo(x, y) : xResp.lineTo(x, y);
  }
  xResp.strokeStyle = '#47DF7F'; xResp.lineWidth = 2.2;
  xResp.shadowColor = '#47DF7F'; xResp.shadowBlur = 10;
  xResp.stroke(); xResp.shadowBlur = 0;
}

// ════════════════════════════════════════════════════════════════════
//  CANVAS — Batimentos ECG
// ════════════════════════════════════════════════════════════════════
const cCard = document.getElementById('cardCanvas');
const xCard = cCard.getContext('2d');
let tCard = 0;

function ecg(f) {
  if (f < 0.08) return 0.4 * Math.sin((f / 0.08) * Math.PI);
  if (f < 0.14) return -0.15;
  if (f < 0.17) return 3.5 * Math.sin(((f - 0.14) / 0.03) * Math.PI);
  if (f < 0.22) return -0.35 * Math.sin(((f - 0.17) / 0.05) * Math.PI);
  if (f < 0.48) return 0.55 * Math.sin(((f - 0.22) / 0.26) * Math.PI);
  return 0;
}

function animarCard() {
  const w = cCard.width, h = cCard.height;
  xCard.clearRect(0, 0, w, h);
  const bpm = frame?.batimentos?.bpm || 0;
  const conf= frame?.batimentos?.confianca || 0;
  if (bpm <= 0) { desenharPlano(xCard, w, h); return; }
  tCard += 0.016 * (bpm / 60) * 1.8;
  const escala = (h / 2 - 5) * 0.38 * Math.min(1, conf * 2.5 + 0.2);
  xCard.beginPath();
  for (let x = 0; x <= w; x++) {
    const t = tCard + (x / w) * 3;
    const y = h / 2 - escala * ecg((t * (bpm / 60)) % 1);
    x === 0 ? xCard.moveTo(x, y) : xCard.lineTo(x, y);
  }
  xCard.strokeStyle = '#F5D800'; xCard.lineWidth = 2;
  xCard.shadowColor = '#F5D800'; xCard.shadowBlur = 10;
  xCard.stroke(); xCard.shadowBlur = 0;
}

// ════════════════════════════════════════════════════════════════════
//  CANVAS — Waveform microfone
// ════════════════════════════════════════════════════════════════════
const cOnda = document.getElementById('ondaCanvas');
const xOnda = cOnda.getContext('2d');

function desenharOnda(onda) {
  const w = cOnda.width, h = cOnda.height;
  xOnda.clearRect(0, 0, w, h);
  xOnda.fillStyle = 'rgba(71,223,127,0.02)'; xOnda.fillRect(0, 0, w, h);
  if (!onda || onda.length < 2) { desenharPlano(xOnda, w, h); return; }
  const mx = Math.max(...onda.map(Math.abs)) || 0.001;
  const px = i => (i / (onda.length - 1)) * w;
  const py = v => h / 2 - (v / mx) * (h / 2 - 6);
  xOnda.strokeStyle = 'rgba(71,223,127,0.10)'; xOnda.lineWidth = 1;
  xOnda.beginPath(); xOnda.moveTo(0, h/2); xOnda.lineTo(w, h/2); xOnda.stroke();
  const grad = xOnda.createLinearGradient(0, 0, w, 0);
  grad.addColorStop(0,   'rgba(71,223,127,0.3)');
  grad.addColorStop(0.5, '#47DF7F');
  grad.addColorStop(1,   '#F5D800');
  xOnda.beginPath();
  onda.forEach((v, i) => i === 0 ? xOnda.moveTo(px(i), py(v)) : xOnda.lineTo(px(i), py(v)));
  xOnda.strokeStyle = grad; xOnda.lineWidth = 1.5;
  xOnda.shadowColor = '#47DF7F'; xOnda.shadowBlur = 4;
  xOnda.stroke(); xOnda.shadowBlur = 0;
}


// ════════════════════════════════════════════════════════════════════
//  CANVAS — HackRF histórico canais
// ════════════════════════════════════════════════════════════════════
const cHrf = document.getElementById('hrfCanvas');
const xHrf = cHrf ? cHrf.getContext('2d') : null;
const HRF_CORES  = ['#47DF7F','#F5D800','#ff6060'];
const HRF_LABELS = ['CH1 2412','CH6 2437','CH11 2462'];
const HRF_CANAIS = ['1','6','11'];

function hexToRgb(h) {
  return `${parseInt(h.slice(1,3),16)},${parseInt(h.slice(3,5),16)},${parseInt(h.slice(5,7),16)}`;
}

function desenharHackRFCanvas(hrf) {
  if (!xHrf || !cHrf) return;
  const wrap = $('hrfCanvasWrap');
  const canais = hrf?.canais || {};
  const temDados = HRF_CANAIS.some(c => canais[c]?.historico?.length > 1);
  if (!temDados) { if (wrap) wrap.style.display='none'; return; }
  if (wrap) wrap.style.display='block';

  const w = cHrf.width, h = cHrf.height;
  xHrf.clearRect(0, 0, w, h);
  let todos = [];
  HRF_CANAIS.forEach(c => { if (canais[c]?.historico) todos = todos.concat(canais[c].historico); });
  if (todos.length === 0) return;
  const mn = Math.min(...todos)-2, mx = Math.max(...todos)+2;
  const py = v => h-12-((v-mn)/(mx-mn))*(h-24);

  xHrf.strokeStyle='rgba(71,223,127,0.06)'; xHrf.lineWidth=1;
  for (let i=0;i<=4;i++) {
    const y=12+(i/4)*(h-24);
    xHrf.beginPath(); xHrf.moveTo(40,y); xHrf.lineTo(w,y); xHrf.stroke();
    xHrf.fillStyle='rgba(71,223,127,0.30)'; xHrf.font='9px "JetBrains Mono",monospace';
    xHrf.fillText(`${Math.round(mx-(i/4)*(mx-mn))}`,2,y+4);
  }

  HRF_CANAIS.forEach((c,ci) => {
    const hist = canais[c]?.historico || [];
    if (hist.length < 2) return;
    const cor = HRF_CORES[ci];
    const n   = hist.length;
    const px  = i => 40+((i/(n-1))*(w-44));
    const rgb = hexToRgb(cor);
    xHrf.beginPath();
    hist.forEach((v,i) => i===0 ? xHrf.moveTo(px(i),py(v)) : xHrf.lineTo(px(i),py(v)));
    xHrf.lineTo(px(n-1),h); xHrf.lineTo(px(0),h); xHrf.closePath();
    const ag = xHrf.createLinearGradient(0,0,0,h);
    ag.addColorStop(0,`rgba(${rgb},0.18)`); ag.addColorStop(1,`rgba(${rgb},0.01)`);
    xHrf.fillStyle=ag; xHrf.fill();
    xHrf.beginPath();
    hist.forEach((v,i) => i===0 ? xHrf.moveTo(px(i),py(v)) : xHrf.lineTo(px(i),py(v)));
    xHrf.strokeStyle=cor; xHrf.lineWidth=1.6;
    xHrf.shadowColor=cor; xHrf.shadowBlur=5;
    xHrf.stroke(); xHrf.shadowBlur=0;
    xHrf.fillStyle=cor; xHrf.font='9px "JetBrains Mono",monospace';
    xHrf.fillText(HRF_LABELS[ci],42,12+ci*14);
  });
}

// ════════════════════════════════════════════════════════════════════
//  Utilitários
// ════════════════════════════════════════════════════════════════════
function $(id) { return document.getElementById(id); }

function desenharPlano(ctx, w, h) {
  ctx.strokeStyle='rgba(71,223,127,0.12)'; ctx.lineWidth=1;
  ctx.setLineDash([4,6]);
  ctx.beginPath(); ctx.moveTo(0,h/2); ctx.lineTo(w,h/2); ctx.stroke();
  ctx.setLineDash([]);
}

// ════════════════════════════════════════════════════════════════════
//  Loop de animação
// ════════════════════════════════════════════════════════════════════
function animar() {
  requestAnimationFrame(animar);
  desenharWaterfall();
  desenharEspectroLinha();
  desenharRSSI();
  animarPresenca();
  animarResp();
  animarCard();
  desenharOnda(frame?.audio?.onda);
  desenharRadar();
  desenharEcho();
  desenharDoppler();
  desenharHackRFCanvas(frame?.hackrf);
}
animar();
