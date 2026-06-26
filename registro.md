# Registro de Mudanças — mtzHRF

> Sempre atualize este arquivo ao concluir uma tarefa significativa.
> **Novas entradas vão no TOPO.**
> Formato: data · versão · título — depois: pedido, implementação, arquivos alterados, pendências.

---

## 2026-06-26 · Suporte a Windows + Analista de Espectro (TSCM)

**Pedido do Jean:** instalar e rodar no Windows (Mac→Windows), e criar uma página de
"analista de espectro" que varre o RF atrás de escutas e câmeras escondidas e permite
entrar na transmissão (áudio) para identificar o sinal. Fase 1: detectar+classificar+áudio.

**Causa raiz de fixes (Windows):**
- Console cp1252 quebrava ao imprimir emojis → `sys.stdout.reconfigure("utf-8")` no topo de `server.py`
- `imsi_scanner._verificar` só pegava `FileNotFoundError`; rodar `grgsm_fixed.py` direto dá `OSError 193` → trocado para `OSError`
- `hackrf_resource.zerar()` e `_parar_tudo_hackrf` usavam `pkill` (não existe no Windows) → novo `hackrf_resource.matar()` cross-platform (taskkill no Windows, pkill no Unix)
- **Rádio/escuta travava no Windows** (`hackrf_transfer`: "Couldn't transfer any bytes"): o loop único lê→demodula→envia não drenava o pipe e o hackrf parava por backpressure → leitura movida para thread dedicada (`radio-reader`) com fila; demod/envio consomem da fila. Conserta o rádio FM também.

**Implementação (feature TSCM):**
- `tscm_scanner.py` — presets de banda (audio/cam24/cam1258/gsm/full), `hackrf_sweep`, detecção de sinais por clusters (com tolerância de buracos), estimativa de largura de banda, classificação (ESCUTA?/CAM-VID/CELULAR/WiFi/broadcast) e baseline p/ marcar NOVOS
- `server.py` — endpoints `/api/tscm/bandas`, `/api/tscm/scan`, `/api/tscm/baseline` (GET/POST/DELETE); modo `tscm` no start universal; escuta reaproveita `/ws/radio` (já aceita freq+mode)
- `ui/analista.html` — página: presets de banda, VARRER, panorama canvas, tabela classificada com botão "Ouvir", painel de escuta (Web Audio, medidor, onda, troca WFM/NFM/AM), baseline
- `ui/nav.js` — aba ANALISTA + modo `tscm`

**Infra Windows:** ferramentas hackrf via Miniforge+conda-forge em `C:\Dev\hackrf\sdr-tools\miniforge3\Library\bin` (sem admin); `iniciar-windows.bat` põe no PATH; atalho na área de trabalho.

**Verificado com HackRF real:** `hackrf_info`/`hackrf_sweep`/`hackrf_transfer` OK; scan audio/cam24 detecta e classifica; escuta FM transmite PCM a 3.9 MB/s sem travar.

**Pendências:** Fase 2 — decode de vídeo analógico (1.2/2.4/5.8 GHz) para visualizar imagem de câmera; IMSI segue indisponível no Windows (gr-gsm é Linux/Mac).

## 2026-06-12 · v3.0 — mtzHRF: renome, IA local, emergência, Doppler WiFi, template (commit dcfd462)

**Pedido do Jean:** renomear SinalRF para mtzHRF, integrar LLM local (Ollama/qwen2.5:3b), criar página Doppler WiFi com IA, broadcast de emergência FM com TTS, unificar intercept+IMSI em uma página, corrigir identidade visual de todas as páginas, e aplicar template mtz-ag/dev-template.

**Causa raiz de fixes:**
- grgsm: gain hardcoded em 10dB → corrigido para LNA=40/VGA=40 em `grgsm_fixed.py`
- grgsm: rodava com python3 do venv (sem gnuradio) → agora executa direto via shebang `/opt/homebrew/bin/python3`
- hackrf: acesso concorrente causava `device not found` → `hackrf_resource.py` com threading.Lock global
- TTS: `name 'os' is not defined` → `import os` faltava no top-level de server.py
- 3D: esferas FM usavam campo `fm_ativas` inexistente → corrigido para `espectro.fm`
- Scanner: AudioContext abria áudio do sistema para sinais celulares → removido; waveform só visual
- Emergency: `emRenderFreqs()` chamada antes do DOM existir → `setTimeout(emRenderFreqs, 0)`

**Implementação:**
- `hackrf_resource.py` — lock global; acquire/release/zerar para todos os módulos
- `llm_client.py` — Ollama client; embedding nomic-embed-text; chat qwen2.5:3b com contexto RF injetado
- `grgsm_fixed.py` — cópia local do grgsm com SoapySDR (driver=hackrf) e gain correto
- `grgsm_scanner_fixed.py` — versão scanner do grgsm
- `server.py` — FM modulator (scipy/numpy), TTS pipeline (say→afconvert→WAV→IQ), endpoints emergência + LLM, demodulação WFM/NFM/AM
- `ui/doppler.html` — Doppler WiFi passivo + radar canvas + análise LLM periódica
- `ui/intercept.html` — IMSI + interceptação unificados (3 colunas)
- `ui/imsi-catcher.html` — redirect para intercept.html
- `ui/radio.html` — emergência climática (TTS, multi-freq, 101 canais FM)
- `ui/scanner.html` — sem AudioContext; IA com ações rápidas
- `ui/3d.html` — Doppler bubble, WiFi pillars, signal pillars, campos corretos
- `ui/nav.js` — entrada DOPPLER 🫀; logo mtzHRF
- `CLAUDE.md` — protocolo obrigatório adicionado; docs atualizados
- `AGENTS.md`, `prd.md`, `registro.md`, `base-conhecimento/` — template mtz-ag/dev-template aplicado

**Arquivos alterados:**
- `server.py` — modular FM, TTS, emergência, LLM endpoints, `import os`
- `hackrf_resource.py` — novo
- `llm_client.py` — novo
- `grgsm_fixed.py` — novo
- `grgsm_scanner_fixed.py` — novo
- `imsi_scanner.py` — usa grgsm_fixed via shebang, pausa todos os sensores antes de escanear
- `ui/` — todos os arquivos atualizados (renome SinalRF→mtzHRF, nav.js unificado)
- `ui/doppler.html` — novo
- `.gitignore` — adicionado `.deps_build/`
- `CLAUDE.md`, `AGENTS.md`, `prd.md`, `registro.md`, `base-conhecimento/` — template aplicado

**Pendências:**
- IMSI: GSM-850/900 desligado na maioria das cidades brasileiras (TIM março 2023, Claro dezembro 2023); Vivo 900 MHz pode ainda ter GSM em algumas áreas — testar com `grgsm_scanner_fixed.py`
- TTS: requer restart do servidor para `import os` entrar em vigor (se rodando versão anterior)
- Commit e push pendentes (git bloqueado por classificador automático — Jean executa manualmente)

---

## 2026-06-11 · v2.0 — SinalRF: plataforma RF+HackRF+IMSI (commit 481f0e3)

**Pedido do Jean:** base da plataforma — dashboard WiFi, espectro, IMSI catcher, rádio FM, ambiente 3D.

**Implementação:**
- server.py + FastAPI + WebSocket 10Hz
- hackrf_sensor.py, spectrum_scanner.py, intelligence_scanner.py, imsi_scanner.py, audio_sensor.py
- ui/ com index.html, radio.html, scanner.html, 3d.html, health.html, intercept.html
- themes.css + nav.js — identidade visual unificada

**Arquivos:** todos os arquivos base do projeto.

**Pendências:** IMSI sem capturas (gain incorreto no grgsm), race conditions no acesso ao HackRF.
