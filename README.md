# 📡 mtzRF

**Plataforma de sensoriamento RF em tempo real para HackRF One.**

mtzRF concentra numa única interface web local todas as capacidades do HackRF One —
espectro, presença por Doppler, rádio FM, interceptação GSM/IMSI, ambiente RF 3D e
análise por IA local — sem depender de nuvem. Backend em Python + FastAPI, frontend
em HTML/JS puro, com streaming de dados via WebSocket a 10 Hz.

> ⚠️ **Uso responsável.** Captura/interceptação de tráfego celular (IMSI) e transmissão
> de RF são reguladas por lei na maioria dos países. Use apenas em laboratório, em
> espectro próprio/autorizado e de acordo com a legislação local. O autor não se
> responsabiliza por uso indevido.

---

## ✨ Funcionalidades

- **Dashboard em tempo real** — WiFi RSSI, Doppler corporal, presença, respiração e batimentos
- **Rádio FM** (WFM/NFM/AM) com nomes de estações brasileiras
- **Scanner de espectro** 88–6000 MHz com categorização (FM, celular, WiFi, 5G…) e histórico waterfall
- **Analista de Espectro (TSCM)** — varre faixas de escutas/câmeras (VHF/UHF, 1.2/2.4/5.8 GHz, GSM), classifica cada sinal (provável escuta, câmera, WiFi, celular), **entra na transmissão de áudio** para identificar e tenta **decodificar a imagem de câmeras analógicas FM** (NTSC/PAL, monocromático, experimental); baseline p/ destacar sinais novos
- **Câmeras IP/WiFi (rede)** — escaneia a rede do PC e lista todos os dispositivos com **IP, MAC, fabricante e portas** (RTSP/ONVIF), destacando as câmeras. Não usa HackRF — é o único jeito de obter o IP de câmeras WiFi (o tráfego RF é criptografado)
- **WiFi Red Team (testes autorizados)** — gerenciador de adaptadores, **recon WiFi** (APs, canal, segurança, sinal), **detecção de rogue AP/evil-twin** e **portal cativo de conscientização** anti-phishing (captura local + revelação educativa, só com campanha autorizada). Sem deauth/jamming. Uso restrito a redes próprias ou com autorização escrita
- **Ambiente RF 3D** — canvas 2D com projeção em perspectiva (pilares, esferas, partículas)
- **Doppler WiFi passivo** + análise por LLM local
- **Interceptação GSM/IMSI** via gr-gsm (auto-scan das torres mais fortes)
- **IA local** (Ollama / qwen2.5:3b) com contexto RF injetado
- **Broadcast de emergência** — TTS → modulação FM → transmissão multi-frequência (+ SMS via Twilio)
- **Lock global do HackRF** — garante que apenas um processo acessa o rádio por vez

---

## 🏗️ Arquitetura

```
server.py                 ← FastAPI + WebSocket hub (broadcast 10 Hz)
│
├── hackrf_sensor.py        ← WiFi 2.4 GHz + Doppler corporal
├── spectrum_scanner.py     ← Espectro 88–900 MHz (hackrf_sweep)
├── intelligence_scanner.py ← Inteligência 88–6000 MHz (hackrf_sweep)
├── imsi_scanner.py         ← GSM IMSI/TMSI (gr-gsm)
├── audio_sensor.py         ← Microfone → respiração + batimentos
├── hackrf_resource.py      ← Lock global de acesso ao HackRF (CRÍTICO)
├── llm_client.py           ← Cliente Ollama (chat + embeddings)
└── grgsm_fixed.py          ← Cópia local do grgsm com gain correto

ui/
├── index.html      ← Dashboard          radio.html     ← Rádio FM
├── scanner.html    ← Scanner espectro   3d.html        ← Ambiente RF 3D
├── health.html     ← Monitor de saúde   intercept.html ← IMSI/TMSI
├── doppler.html    ← Doppler WiFi        emergencia.html← Broadcast emergência
├── themes.css / style.css ← Design system        nav.js / app.js ← Frontend JS
```

Detalhes técnicos completos estão em [`CLAUDE.md`](CLAUDE.md) e em [`base-conhecimento/`](base-conhecimento/).

---

## 📋 Requisitos

| Item | Necessário para |
|------|-----------------|
| **Python 3.10+** | tudo (núcleo da plataforma) |
| **HackRF One** + `hackrf` tools (`hackrf_info`, `hackrf_sweep`, `hackrf_transfer`) | espectro, Doppler, rádio, IMSI |
| **gr-gsm** (`grgsm_livemon_headless`) | interceptação IMSI/GSM |
| **Ollama** + `qwen2.5:3b` | análise por IA local (opcional) |
| **Conta Twilio** | SMS de emergência (opcional) |
| **Microfone** | sensor de respiração/batimentos por áudio (opcional) |

> **Modo simulação:** sem HackRF conectado, a plataforma **roda mesmo assim** — a interface
> web funciona, e os sensores que dependem do rádio ficam inativos. Ótimo para desenvolver
> o frontend ou testar a instalação. Funciona em **Windows, macOS e Linux**.

---

## 🚀 Instalação e execução

### Windows

```powershell
git clone https://github.com/jpmortaza/sinalrf.git
cd sinalrf
```

Depois é só dar **duplo clique em `iniciar-windows.bat`** (ele cria o ambiente virtual,
instala as dependências, coloca as ferramentas do HackRF no PATH e sobe o servidor).
Ou, manualmente:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe server.py
```

**Ferramentas do HackRF no Windows** — o `hackrf_info`/`hackrf_sweep`/`hackrf_transfer`
não vêm com o Windows. A forma mais simples (sem privilégios de administrador) é via
[Miniforge](https://github.com/conda-forge/miniforge) + conda-forge:

```powershell
# instala o conda por usuário (JustMe) e depois o pacote hackrf
.\Miniforge3.exe /InstallationType=JustMe /RegisterPython=0 /S /D=C:\Dev\hackrf\sdr-tools\miniforge3
C:\Dev\hackrf\sdr-tools\miniforge3\Scripts\conda.exe install -y -c conda-forge hackrf
```

Isso instala os binários em `...\miniforge3\Library\bin`. Confirme o dispositivo com
`hackrf_info` (deve mostrar `Found HackRF`). O driver USB (WinUSB) é vinculado
automaticamente pelo firmware do HackRF One — se não for, use o [Zadig](https://zadig.akeo.ie/)
para instalar o WinUSB no dispositivo. O `iniciar-windows.bat` já adiciona essa pasta ao PATH.

> A interceptação IMSI depende de **gr-gsm**, que não roda no Windows. No Windows, as
> funções de espectro, Doppler/WiFi e rádio FM funcionam; para IMSI use macOS/Linux —
> ou rode o gr-gsm via **WSL2** seguindo [`docs/IMSI-WSL2-grgsm.md`](docs/IMSI-WSL2-grgsm.md)
> (uso legal/autorizado apenas).

### macOS / Linux

```bash
git clone https://github.com/jpmortaza/sinalrf.git
cd sinalrf

# Com uv (recomendado):
uv run python server.py

# Ou com venv:
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python server.py
```

No macOS também existe o launcher **`🚀 Iniciar mtzRF.command`** (duplo clique no Finder).

Com o servidor no ar, acesse: **http://localhost:8765**

---

## 🌐 Páginas

| URL | Página |
|-----|--------|
| `http://localhost:8765/` | Dashboard (presença, WiFi, Doppler, saúde) |
| `/radio.html` | Rádio FM |
| `/scanner.html` | Scanner de espectro / inteligência |
| `/analista.html` | **Analista de Espectro (TSCM)** — varre escutas/câmeras e escuta a transmissão |
| `/rede.html` | **Câmeras IP/WiFi** — escaneia a rede e lista dispositivos/câmeras (IP, MAC, fabricante, portas) |
| `/wifi.html` | **WiFi Red Team** — adaptadores, recon WiFi, detecção de rogue AP e portal de conscientização (uso autorizado) |
| `/3d.html` | Ambiente RF 3D |
| `/health.html` | Monitor de saúde RF |
| `/doppler.html` | Doppler WiFi + IA |
| `/intercept.html` | Interceptação IMSI/TMSI |
| `/emergencia.html` | Broadcast de emergência |

---

## ⚙️ Configuração

Variáveis de ambiente opcionais (SMS de emergência via Twilio). Copie o exemplo e preencha:

```bash
cp .env.example .env
```

```ini
TWILIO_ACCOUNT_SID=ACxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxx
TWILIO_FROM_NUMBER=+14155551234   # número Twilio verificado (E.164)
```

O arquivo `.env` **não** é versionado (ver `.gitignore`).

### Portas usadas

| Porta | Uso |
|-------|-----|
| `8765` | HTTP + WebSocket (toda a aplicação) |
| `4729` | UDP GSMTAP (IMSI — escuta local) |
| `4730` | UDP grgsm serverport |

---

## 🔌 Endpoints WebSocket

| Canal | Frequência | Conteúdo |
|-------|-----------|----------|
| `/ws` | 10 Hz | loop principal: RSSI, presença, respiração, batimentos, WiFi, Doppler, espectro |
| `/ws/intel` | ~12 s | inteligência de espectro 88–6000 MHz categorizada |
| `/ws/imsi` | 2 s | torres GSM, capturas IMSI/TMSI, operadoras |

A estrutura completa de cada payload está documentada em [`CLAUDE.md`](CLAUDE.md#websocket-data-structures).

---

## 🛠️ Regra de ouro do HackRF

O HackRF One **não pode ser compartilhado** — apenas um processo abre o dispositivo por vez.
Todos os acessos passam por [`hackrf_resource.py`](hackrf_resource.py), que mantém um lock
global. Ao adicionar um novo sensor, sempre faça `acquire(...)` antes do subprocess e
`release()` no `finally`. Veja o detalhamento em [`CLAUDE.md`](CLAUDE.md#hackrf--regra-de-ouro).

---

## 🐛 Problemas comuns

| Sintoma | Causa | Solução |
|---------|-------|---------|
| `hackrf device not found` num sensor | outro processo abriu o HackRF | `hackrf_resource.zerar()` libera todos |
| IMSI: 0 capturas após 15 s | frequência sem GSM ativo (LTE na mesma banda) | o auto-scan tenta a próxima torre |
| Rádio FM não inicia | HackRF em uso | pare o IMSI primeiro |
| Console quebra com erro de encoding no Windows | cp1252 não suporta emojis | já tratado (UTF-8 forçado no `server.py` e no `.bat`) |

Mais casos em [`CLAUDE.md`](CLAUDE.md#problemas-comuns).

---

## 📄 Licença / autoria

Projeto pessoal de **Jean Mortaza** — operador único, uso em campo e laboratório.
Sem licença pública definida; entre em contato antes de reutilizar.
