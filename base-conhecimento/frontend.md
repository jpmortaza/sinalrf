# Frontend — mtzRF

## Stack

HTML5 + CSS3 + JavaScript ES2022 — **sem framework, sem bundler.**
Todas as páginas são estáticas servidas pelo FastAPI.

## Design system

`ui/themes.css` — variáveis CSS compartilhadas entre todas as páginas.

| Tema | Como ativar |
|------|-------------|
| Preto (terminal) | padrão — `data-theme` ausente |
| Neutro (IDE) | `data-theme="neutral"` no `<html>` |

Toggle persiste em `localStorage` via `nav.js`.

## Nav unificada

`ui/nav.js` — injetada em todas as páginas via `<script src="/nav.js">`.

**API pública:**
```javascript
window.srfNav.setWs(true|false)   // ponto verde/cinza WS
window.srfNav.setHrf(true|false)  // badge HRF ATIVO / HRF —
```

**Páginas no menu:**
| Label | Arquivo |
|-------|---------|
| DASHBOARD | index.html |
| RÁDIO FM | radio.html |
| SCANNER | scanner.html |
| 3D RF | 3d.html |
| DOPPLER | doppler.html |
| SAÚDE | health.html |
| IMSI · INTCP | intercept.html |

## Estrutura de página padrão

```html
<head>
  <link rel="stylesheet" href="/themes.css">
</head>
<body>
  <!-- nav.js injeta #srf-nav aqui automaticamente -->
  <script src="/nav.js"></script>

  <!-- conteúdo da página -->

  <script>
    // WebSocket connection
    // srfNav.setWs(true) quando conectado
    // srfNav.setHrf(true) quando hackrf.disponivel
  </script>
</body>
```

## 3D RF Environment (`3d.html`)

Canvas 2D com projeção perspectiva manual (NÃO WebGL).

**Funções de desenho:**
- `drawGrid()` — grade XZ no chão
- `drawPresenceRing()` — anel deformado por WiFi variância + presença
- `drawDopplerBubble(t)` — bolha respiratória (hackrf.doppler.variancia, resp_bpm)
- `drawWiFiPillars(t)` — 3 pilares CH1/6/11
- `drawSignalPillars(t)` — pilares de intel.sinais (angle=freq, height=dbm)
- `drawRays()` — 64 raios de espectro.freqs/dbs
- `drawSignalSpheres()` — esferas FM + fantasmas (espectro.fm / espectro.anomalos)

**Campos corretos (bugs históricos corrigidos):**
| Errado | Correto |
|--------|---------|
| `intelData.fm_ativas` | `liveData.espectro.fm` |
| `intelData.anomalos` | `liveData.espectro.anomalos` |
| `(s.freq / 1e6).toFixed(2)` | `s.freq_mhz.toFixed(1)` |

## Scanner (`scanner.html`)

- **SEM AudioContext** — não abre áudio do sistema
- Waveform de demodulação é só visualização (canvas)
- Para escutar, abre `radio.html` numa nova aba
- `NO_AUDIO_CATS`: CELULAR, WiFi-2G, 5G, ISM-2G, GNSS, DAB, SAT-MET, ISM-433, DESCONHECIDO

## Doppler (`doppler.html`)

- Usa `/ws` (main loop) — dados passivos, sem acionar HackRF adicionalmente
- Radar canvas animado + breathing wave + ECG
- AI panel: análise automática a cada 30s via `/api/llm/chat`

## Interceptação (`intercept.html`)

- Unificação de intercept.html + imsi-catcher.html (imsi-catcher.html agora é redirect)
- Layout 3 colunas: sidebar (torres + controles) | main (capturas + timeline) | right (operadoras + protocolos)
- `freq_atual` exibida na status bar durante auto-scan
