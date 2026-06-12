# Integrações — mtzHRF

> NUNCA escreva valores de chaves ou senhas aqui — apenas onde ficam guardadas.

## hackrf_transfer / hackrf_sweep

Binários CLI do projeto HackRF (libhackrf). Instalados via Homebrew.

- `hackrf_transfer` — captura/transmissão de IQ bruto
- `hackrf_sweep` — varredura de espectro rápida
- Ambos exigem acesso exclusivo ao USB — gerenciado por `hackrf_resource.py`

## grgsm (GNU Radio GSM)

**Local:** `grgsm_fixed.py` e `grgsm_scanner_fixed.py` na raiz do projeto.

São cópias locais com dois patches críticos em relação ao upstream:
1. Usa `gnuradio.soapy` com `driver=hackrf` — NÃO osmosdr
2. Gain: LNA=40, VGA=40 (upstream hardcoda 10dB — insuficiente para captura real)

**Python:** `/opt/homebrew/bin/python3` obrigatório (tem gnuradio instalado).
**Log:** `grgsm.log` — sempre verificar se grgsm crashar.

## Ollama (LLM local)

**URL:** `http://localhost:11434`
**Modelos usados:**
- `qwen2.5:3b` — chat principal com contexto RF
- `nomic-embed-text` — embeddings para classificação semântica de sinais

**Integração em:** `llm_client.py`

**Endpoints usados:**
- `POST /api/chat` — chat com streaming
- `POST /api/embeddings` — embeddings para nomic-embed-text
- `GET /api/tags` — lista modelos disponíveis

**Contexto RF injetado automaticamente:** frequências ativas, sinais detectados, estado dos sensores.

## macOS TTS (say)

**Comando:** `say -v Luciana -o output.aiff -- "texto"`

Vozes pt-BR disponíveis: Luciana (feminino), Eddy, Flo, Reed

**Conversão para WAV:**
```bash
afconvert -f WAVE -d LEI16@22050 input.aiff output.wav
```

Implementado em `server.py._tts_para_wav()` com `tempfile.TemporaryDirectory`.

## hackrf_transfer TX (broadcast emergência)

```bash
hackrf_transfer -t iq_file.bin -f 101000000 -s 2000000 -x 47
```

- `-f` — frequência em Hz
- `-s 2000000` — sample rate 2 Msps
- `-x 47` — TX VGA gain (0–47 dB)
- Arquivo IQ: int8 interleaved (I, Q, I, Q...)

O FM modulator (`server.py._modular_fm_iq()`) gera esse formato diretamente da WAV.
