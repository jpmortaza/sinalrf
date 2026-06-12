# Backend — mtzHRF

## Stack

Python 3.11+ · FastAPI · uvicorn · WebSocket nativo · uv (gerenciador de deps)

**Iniciar:**
```bash
cd /Users/jeanmortaza/Documents/#all/dev/hackRF
uv run python server.py
```

## Portas

| Porta | Protocolo | Uso |
|-------|-----------|-----|
| 8765 | HTTP + WS | Tudo: API REST + todos os WebSockets |
| 4729 | UDP | GSMTAP — pacotes GSM do grgsm recebidos pelo servidor |
| 4730 | UDP | serverport interno do grgsm (Wireshark debug) |

## WebSocket endpoints

| Endpoint | Frequência | Conteúdo |
|----------|------------|----------|
| `/ws` | 10 Hz | Main loop: presença, WiFi, espectro, áudio, Doppler |
| `/ws/intel` | ~12s | Varredura inteligente 88–6000 MHz |
| `/ws/imsi` | 2s | Torres GSM + capturas IMSI/TMSI |
| `/ws/radio` | streaming | PCM de áudio demodulado (WFM/NFM/AM) |
| `/ws/llm` | streaming | Chat com LLM local (qwen2.5:3b) |

## API REST principal

| Endpoint | Método | O que faz |
|----------|--------|-----------|
| `/api/emergencia/preparar` | POST | TTS → WAV → FM IQ (prepara buffer) |
| `/api/emergencia/transmitir` | POST | TX sequencial multi-frequência |
| `/api/emergencia/parar` | POST | Para broadcast em andamento |
| `/api/emergencia/status` | GET | Estado atual do broadcast |
| `/api/llm/status` | GET | Modelos Ollama disponíveis |
| `/api/llm/chat` | POST | Chat único com contexto RF |
| `/api/llm/classificar` | POST | Classifica sinal por embedding |
| `/api/radio/modo` | GET | Modo atual de demodulação |
| `/api/imsi/iniciar` | POST | Inicia captura IMSI |
| `/api/imsi/parar` | POST | Para captura IMSI |
| `/api/imsi/limpar` | POST | Limpa histórico de capturas |

## FM Modulator

Pipeline completo em Python (sem dependências de SO além do scipy):
```
PCM 22050Hz → resample para 2Msps → pre-emphasis 50µs → integral de fase → e^(j*fase) → IQ int8
```

Implementado em `server.py._modular_fm_iq()`. Desvio: 75 kHz (padrão WFM).

## TTS Pipeline (emergência)

```
say -v Luciana → tts.aiff → afconvert → tts.wav → _modular_fm_iq() → IQ bytes → hackrf_transfer TX
```

Vozes disponíveis (pt-BR): Luciana, Eddy, Flo, Reed

## Demodulação multi-mode

| Modo | Algoritmo |
|------|-----------|
| WFM | Phase discriminator + de-emphasis 75µs |
| NFM | Phase discriminator + LPF 6kHz + voice bandpass |
| AM | Envelope detection + DC removal + voice bandpass |

Mapeamento automático por categoria em `_CAT_MODO`: FM→WFM, AERONAV→AM, VHF/NFM→NFM.
