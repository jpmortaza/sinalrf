# Registro de Mudanças — mtzRF

> Sempre atualize este arquivo ao concluir uma tarefa significativa.
> **Novas entradas vão no TOPO.**
> Formato: data · versão · título — depois: pedido, implementação, arquivos alterados, pendências.

---

## 2026-06-26 · Fix TTS cross-platform + firmware do HackRF

**TTS de emergência multiplataforma:** `_tts_para_wav` usava `say`/`afconvert` (só macOS)
→ no Windows dava `WinError 2`. Agora detecta a plataforma: Windows usa SAPI
(System.Speech via PowerShell, prefere voz pt-BR), macOS mantém say+afconvert, Linux usa
espeak-ng/espeak. Verificado: gera WAV mono 22050/16-bit e o modulador FM lê OK.

**Firmware do HackRF atualizado:** 2021.03.1 (API 1.04) → 2026.01.3 (API 1.10), via
`hackrf_spiflash` com o firmware oficial (release v2026.01.3), alinhando com libhackrf
2026.01.3. Device verificado operando (sweep real pós-update).

## 2026-06-26 · WiFi Red Team (testes autorizados)

**Pedido do Jean:** aba para testes de WiFi, principalmente engenharia social (portal falso
estilo evilportal). Contexto: engajamento autorizado de cliente, só Windows por enquanto,
múltiplos adaptadores (placa interna + USB Realtek). Construir tudo de uma vez.

**Fronteira ética aplicada:** uso só em redes próprias/autorizadas; salvaguardas embutidas
(consentimento, log local marcado como teste, revelação educativa). **NÃO** foi feito
deauth/jamming (negação de serviço). Evil-twin completo não roda no Windows (sem modo
monitor/múltiplos APs) — documentado que precisa de Linux/hostapd ou ESP32.

**Implementação (`wifi_tools.py`, `server.py`, `ui/wifi.html`, `portais/`):**
- Adaptadores: `netsh wlan show interfaces`+`drivers`, parsing tolerante a locale (strip-accents)
- Recon: `netsh wlan show networks mode=bssid` → SSID/BSSID/sinal/canal/segurança (dedup por BSSID,
  SSID oculto tratado)
- Detecção de rogue AP: agrupa por SSID; flagra segurança mista (evil-twin), clone aberto e
  múltiplos fabricantes
- Portal cativo: templates `portais/wifi.html` e `portais/google.html` (standalone, captura→
  revelação "isto foi phishing"); endpoint `/portal/{nome}`; captura SÓ grava se a campanha
  estiver ARMADA com autorização; log local `capturas_portal.jsonl` (gitignored)
- Endpoints: `/api/wifi/adaptadores|scan|portais|portal/arm|portal/desarmar|captura|capturas`
- `ui/nav.js`: aba "WIFI · RT" (modo idle)

**Causa raiz de fix:** Windows 11 exige **Serviços de Localização ativados** (e às vezes admin)
para `netsh` ler WiFi — sem isso retorna vazio com "permissão de local"/"exige elevação".
Adicionado `wifi_tools.checar_acesso()` que detecta e a UI mostra o aviso; após ativar a
Localização o recon funcionou (21 APs, rogue detectado, ambas as placas listadas).

**Verificado:** 2 adaptadores (Realtek 8821CE PCIe conectada + RTL8192EU USB), 21 APs no recon,
rogue AP flagrado, portal serve + captura gated por autorização funcionando.

**Pendências:** OUI de AP não mapeado; evil-twin real/monitor mode/handshake fora do Windows
(precisa Linux/ESP32); QR code do portal não gerado (mostra URL+copiar).

## 2026-06-26 · Câmeras IP/WiFi — scanner de rede

**Pedido do Jean:** para câmeras WiFi, quero o IP e a rede em que estão conectadas.

**Decisão técnica:** o HackRF NÃO obtém IP de câmera WiFi (tráfego 802.11 criptografado;
o IP fica nos dados cifrados). O IP só se consegue escaneando a rede em que o PC está.
Por isso a feature é um scanner de rede (não-RF), focado na rede do próprio PC. Câmeras em
redes de terceiros exigiriam adaptador em modo monitor e mesmo assim só dariam MAC↔SSID.

**Implementação:**
- `net_scanner.py` — info da rede (IP/SSID/sub-rede via netsh+socket), ping sweep concorrente
  + tabela ARP (filtra broadcast/multicast), varredura de portas de câmera (RTSP 554/8554,
  ONVIF 80/8000/8080, DVR 37777/34567…), hostname por DNS reverso, fabricante por OUI
  (lista curada de Hikvision/Dahua/Reolink/Wyze/Ring/Tuya-Espressif/etc.), classificação de
  câmera por porta RTSP + fabricante
- `server.py` — endpoints `GET /api/rede/info`, `POST /api/rede/scan`
- `ui/rede.html` — página "CÂMERAS IP": info da rede, botão escanear, card de câmeras
  destacadas + tabela de todos os dispositivos (IP, MAC, fabricante, hostname, portas, link abrir)
- `ui/nav.js` — aba CÂMERAS IP (modo idle, não usa HackRF)

**Causa raiz de fix:** sob o servidor (sem console), `subprocess.run(ping, text=True).stdout`
vinha `None`/erro de locale → adicionado `errors="ignore"` e guardas `or ""`.

**Verificado:** escaneou a rede real (192.168.1.0/24) — 7 dispositivos com IP/MAC/hostname/
portas corretos, broadcast filtrado. (Sem câmeras IP nesta rede.)

**Pendências:** OUI é lista curada (fabricantes não-listados aparecem sem nome); detecção
forte vem das portas. Câmeras em outras redes (modo monitor) fora de escopo.

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

**Fase 2 (decode de vídeo analógico) — IMPLEMENTADA:**
- `tscm_video.py` — captura IQ de banda larga (16 Msps via hackrf_transfer -n), demod FM,
  detecção de sync horizontal robusta a ruído (suavização + run-length por duração mínima),
  fatiamento em linhas e reconstrução de frame em tons de cinza (NTSC/PAL, auto). Escolhe o
  trecho de linhas mais bem espaçadas p/ evitar tearing.
- `server.py` — endpoint `/api/tscm/video`
- `ui/analista.html` — botão "📹 vídeo" nos sinais de câmera + painel com canvas, seletor
  NTSC/PAL/auto e recapturar; render via ImageData
- Validado com sinal NTSC sintético (reconstrói barras + gradiente nítidos); em RF real sem
  câmera analógica retorna "sem sincronismo" graciosamente.

**Pendências:** vídeo analógico é monocromático (sem decode de croma NTSC/PAL); câmeras
digitais/WiFi (criptografadas) não são decodificáveis — só detectadas. IMSI segue
indisponível no Windows (gr-gsm é Linux/Mac).

## 2026-06-12 · v3.0 — mtzRF: renome, IA local, emergência, Doppler WiFi, template (commit dcfd462)

**Pedido do Jean:** renomear mtzRF para mtzRF, integrar LLM local (Ollama/qwen2.5:3b), criar página Doppler WiFi com IA, broadcast de emergência FM com TTS, unificar intercept+IMSI em uma página, corrigir identidade visual de todas as páginas, e aplicar template mtz-ag/dev-template.

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
- `ui/nav.js` — entrada DOPPLER 🫀; logo mtzRF
- `CLAUDE.md` — protocolo obrigatório adicionado; docs atualizados
- `AGENTS.md`, `prd.md`, `registro.md`, `base-conhecimento/` — template mtz-ag/dev-template aplicado

**Arquivos alterados:**
- `server.py` — modular FM, TTS, emergência, LLM endpoints, `import os`
- `hackrf_resource.py` — novo
- `llm_client.py` — novo
- `grgsm_fixed.py` — novo
- `grgsm_scanner_fixed.py` — novo
- `imsi_scanner.py` — usa grgsm_fixed via shebang, pausa todos os sensores antes de escanear
- `ui/` — todos os arquivos atualizados (renome mtzRF→mtzRF, nav.js unificado)
- `ui/doppler.html` — novo
- `.gitignore` — adicionado `.deps_build/`
- `CLAUDE.md`, `AGENTS.md`, `prd.md`, `registro.md`, `base-conhecimento/` — template aplicado

**Pendências:**
- IMSI: GSM-850/900 desligado na maioria das cidades brasileiras (TIM março 2023, Claro dezembro 2023); Vivo 900 MHz pode ainda ter GSM em algumas áreas — testar com `grgsm_scanner_fixed.py`
- TTS: requer restart do servidor para `import os` entrar em vigor (se rodando versão anterior)
- Commit e push pendentes (git bloqueado por classificador automático — Jean executa manualmente)

---

## 2026-06-11 · v2.0 — mtzRF: plataforma RF+HackRF+IMSI (commit 481f0e3)

**Pedido do Jean:** base da plataforma — dashboard WiFi, espectro, IMSI catcher, rádio FM, ambiente 3D.

**Implementação:**
- server.py + FastAPI + WebSocket 10Hz
- hackrf_sensor.py, spectrum_scanner.py, intelligence_scanner.py, imsi_scanner.py, audio_sensor.py
- ui/ com index.html, radio.html, scanner.html, 3d.html, health.html, intercept.html
- themes.css + nav.js — identidade visual unificada

**Arquivos:** todos os arquivos base do projeto.

**Pendências:** IMSI sem capturas (gain incorreto no grgsm), race conditions no acesso ao HackRF.
