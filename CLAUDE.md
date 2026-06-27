# CLAUDE.md — mtzRF / HackRF Platform

**GitHub:** https://github.com/jpmortaza/sinalrf

Plataforma de sensoriamento RF em tempo real usando HackRF One.
Backend Python + FastAPI. Frontend HTML/JS puro. WebSocket para streaming de dados.

---

## PROTOCOLO OBRIGATÓRIO ANTES DE QUALQUER EXECUÇÃO

> **Este projeto roda em um Mac dedicado com HackRF físico e pode ter múltiplos agentes/sessões.**
> Antes de implementar, refatorar ou executar qualquer tarefa, SEMPRE verifique:
>
> 1. **`registro.md`** — log de mudanças recentes, decisões e contexto atual
> 2. **`CLAUDE.md`** (este arquivo) — regras, padrões e arquitetura
> 3. **`base-conhecimento/`** — documentação de domínio e decisões técnicas

Se qualquer um desses arquivos tiver sido atualizado desde a última sessão, leia-o antes de prosseguir.

### Ao concluir uma tarefa significativa

Registre no **TOPO** do `registro.md`:
- O que foi pedido e por quem
- O que foi feito (implementação, causa raiz se for fix)
- Decisões tomadas e o porquê
- Arquivos alterados
- Pendências ou riscos identificados

---

---

## Arquitetura

```
server.py          ← FastAPI + WebSocket hub (10 Hz broadcast)
│
├── hackrf_sensor.py       ← WiFi 2.4 GHz scan + Doppler corporal
├── spectrum_scanner.py    ← Espectro 88–900 MHz (hackrf_sweep)
├── intelligence_scanner.py← Inteligência 88–6000 MHz (hackrf_sweep)
├── imsi_scanner.py        ← GSM IMSI/TMSI (grgsm_livemon_headless)
├── audio_sensor.py        ← Microfone → respiração + batimentos
├── hackrf_resource.py     ← Lock global de acesso ao HackRF ← CRÍTICO
└── grgsm_fixed.py         ← Cópia local do grgsm com gain correto
```

```
ui/
├── themes.css       ← Design system compartilhado (tema preto + neutro)
├── nav.js           ← Nav injetada em todas as páginas
├── app.js           ← Dashboard JS (canvas WiFi, espectro, Doppler)
├── index.html       ← Dashboard
├── radio.html       ← FM Radio player
├── scanner.html     ← Spectrum intelligence scanner
├── health.html      ← Monitor de saúde RF
├── 3d.html          ← Ambiente RF 3D
└── intercept.html   ← IMSI/TMSI Interceptação (unificado)
```

---

## HackRF — Regra de Ouro

**O HackRF One não pode ser compartilhado.** Apenas um processo abre o
dispositivo por vez. TODOS os acessos passam por `hackrf_resource.py`.

```python
import hackrf_resource

# Padrão correto:
if not hackrf_resource.acquire('nome-do-modulo', timeout=5.0):
    return None   # HackRF ocupado — pula e tenta depois

try:
    subprocess.run(['hackrf_transfer', ...])
finally:
    hackrf_resource.release()
```

### Prioridade de acesso (mais alto = trava tudo abaixo)

| Módulo | Função | Timeout do lock |
|--------|--------|-----------------|
| `grgsm` (IMSI) | GSM decode exclusivo | minutos — trava tudo |
| `radio` (FM)   | Streaming contínuo   | minutos — trava tudo |
| `clonar/tx`    | Captura/transmissão  | segundos |
| `intel`        | Sweep 88–6000 MHz    | até 120s |
| `espectro`     | Sweep 88–900 MHz     | até 24s |
| `scan`         | WiFi channels        | 300ms × 3 |
| `doppler`      | Doppler 2.4GHz       | 100ms |

### Quando iniciar atividade exclusiva: `hackrf_resource.zerar()`

```python
# Antes de IMSI, FM radio, transmissão:
hackrf_resource.zerar()          # mata todos os processos HackRF
sensor_hackrf.pausar()           # para loops internos
sensor_espectro.pausar()
sensor_intel.pausar()
hackrf_resource.acquire('grgsm') # adquire lock
# ... faz a atividade ...
hackrf_resource.release()        # devolve
sensor_hackrf.retomar()
```

---

## WebSocket Data Structures

### `/ws` — 10 Hz (main loop)

```
{
  ts, frame, fonte,
  rssi, variancia, threshold, historico[80],
  presenca: { detectado, confianca, atividade },
  respiracao: { bpm, confianca, fonte },
  batimentos: { bpm, confianca, fonte },
  audio: { dispositivo, is_airpods, amplitude_db, onda[128],
           resp_audio:{bpm,confianca}, card_audio:{bpm,confianca} },
  hackrf: {
    disponivel, conectado,
    canais: { "1":{potencia_dbm,variancia,historico}, "6":{...}, "11":{...} },
    doppler: { presente, resp_bpm, confianca, variancia, n_amostras }
  },
  espectro: {
    disponivel, conectado, n_pontos, baseline_ok,
    freqs[], dbs[],           ← em Hz
    historico[20][],          ← waterfall (dBm por ponto)
    fm[]: { freq_mhz, dbm, nome },      ← CORRETO (não "fm_ativas")
    anomalos[]: { freq_mhz, dbm, delta_db }  ← CORRETO (não "intelData.anomalos")
  }
}
```

### `/ws/intel` — ~12s

```
{
  disponivel, conectado, sweeps, duracao_sweep,
  panorama: { freqs[], dbm[], baseline[] },  ← freqs em MHz
  sinais[]: {
    freq_mhz,      ← JÁ em MHz — não dividir por 1e6
    dbm, dbm_max, delta_db,
    persistencia,  ← fração de sweeps ativo (0–1)
    cat,           ← "FM"|"CELULAR"|"WiFi-2G"|"5G"|etc.
    cor,           ← hex color
    desc, novo
  },
  categorias: { "FM":{n,dbm_max,cor,desc}, ... },
  descobertos[80]: { freq_mhz, dbm, delta, cat, cor, desc, ts },
  n_ativos
}
```

### `/ws/imsi` — 2s

```
{
  hackrf_ok, grgsm_ok, grgsm_cmd,
  capturando, freq_atual,  ← freq sendo testada no auto-scan
  escaneando,
  torres[]: { banda, freq_mhz, arfcn, dbm, mcc, mnc, lac, cid, operadora, cor },
  capturas[200]: { tipo, valor, mcc, mnc, operadora, cor, arfcn, dbm, ts },
  celulas, stats, n_unicos, operadoras, ts
}
```

---

## Sensores — Como Funcionam

### SensorHackRF (`hackrf_sensor.py`)
- **Thread scan**: WiFi CH 1/6/11 @ 2.4 GHz, cada 5s
- **Thread doppler**: 2.437 GHz @ 8 Hz, extrai BPM respiratório por FFT
- Chama `hackrf_resource.acquire('scan'|'doppler', timeout=3|1)` antes de cada subprocess
- `pausar()` / `retomar()` para yield ao IMSI/radio

### ScannerEspectro (`spectrum_scanner.py`)
- FM: 88–108 MHz @ 150 kHz bins, wideband: 108–900 MHz @ 1 MHz bins
- Adquire lock UMA vez para FM+wideband (não libera entre as duas)
- Baseline adaptativo (~8 min para estabilizar)
- `espectro.fm` e `espectro.anomalos` são os campos corretos

### ScannerInteligente (`intelligence_scanner.py`)
- 88–6000 MHz, 1 sweep completo a cada 12s (pode durar até 120s)
- Adquire lock toda a duração do sweep
- `intel.sinais[].freq_mhz` já em MHz

### ScannerIMSI (`imsi_scanner.py`)
- **Auto-scan**: testa top-20 torres 15s cada
- `zerar()` + `pausar_todos()` antes de iniciar
- Lock `grgsm` é adquirido antes do Popen e liberado após cada tentativa
- grgsm usa `grgsm_fixed.py` local: LNA=40, VGA=40 (fix do bug de gain)
- Debug: ver `grgsm.log` na pasta do projeto

### Rádio FM (`server.py ws_radio`)
- Pré: `zerar()` + `pausar()` todos os sensores
- Adquire lock `radio` antes do Popen
- Libera lock + `retomar()` todos ao desconectar

---

## 3D RF Environment (`ui/3d.html`)

### Canvas 2D com projeção perspectiva manual (NÃO é WebGL)

```
drawGrid()           → grade XZ no chão
drawPresenceRing()   → anel deformado por WiFi variância + presença
drawDopplerBubble()  → bolha respiratória via hackrf.doppler.*
drawSweep()          → varredura radar decorativa
drawSignalPillars()  → pilares de intel.sinais (angle=freq, height=dbm)
drawWiFiPillars()    → 3 pilares para hackrf.canais[1/6/11]
updateDrawParticles()→ 120 partículas turbulência por variância
drawRays()           → 64 raios de espectro.freqs/dbs
drawSignalSpheres()  → esferas FM + fantasmas (espectro.fm / espectro.anomalos)
drawAntenna()        → mastro central (HackRF One)
```

### Campos corretos (bugs históricos corrigidos)

| Errado (antigo) | Correto |
|---|---|
| `intelData.fm_ativas` | `liveData.espectro.fm` |
| `intelData.anomalos` | `liveData.espectro.anomalos` |
| `(s.freq / 1e6).toFixed(2)` | `s.freq_mhz.toFixed(1)` |

---

## Adicionar Novo Sensor

1. Criar `novo_sensor.py` com `iniciar()`, `parar()`, `pausar()`, `retomar()`, `estado() → dict`
2. Importar em `server.py`, instanciar no bloco global
3. Usar `hackrf_resource.acquire('nome', timeout=X)` antes de subprocess, `release()` no finally
4. Adicionar endpoint `/api/novo/...` se precisar
5. Adicionar ao WebSocket broadcast se necessário

---

## Dev — Iniciar Servidor

```bash
cd /Users/jeanmortaza/Documents/#all/dev/hackRF
uv run python server.py
# ou: duplo clique em "🚀 Iniciar mtzRF.command"
```

### Portas
- `8765` → HTTP + WebSocket (todos os endpoints)
- `4729` → UDP GSMTAP (IMSI — escuta local do server)
- `4730` → UDP grgsm serverport (Wireshark commands)

### grgsm
- `grgsm_fixed.py` = cópia local com gain correto (LNA=40, VGA=40)
- Requer Python `/opt/homebrew/bin/python3` (tem gnuradio)
- Log: `grgsm.log` na pasta do projeto

---

## Problemas Comuns

| Sintoma | Causa | Fix |
|---|---|---|
| `hackrf device not found` em sensor | Outro processo abriu o HackRF | `hackrf_resource.zerar()` libera todos |
| IMSI captura e para imediatamente | grgsm crashou | ver `grgsm.log` |
| IMSI: 0 capturas depois de 15s | Frequência sem GSM ativo (LTE na mesma banda) | Auto-scan tenta próxima torre |
| FM radio não inicia | HackRF em uso | Para o IMSI primeiro |
| 3D: esferas FM não aparecem | Bug antigo usando campo errado | Corrigido — usa `espectro.fm` |
| Doppler bubble não aparece | `hackrf.doppler.variancia` < 0.05 | Normal quando não há movimento |
