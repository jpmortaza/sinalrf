# Sensores — mtzRF

## SensorHackRF (`hackrf_sensor.py`)

**Função:** WiFi passivo (RSSI 2.4 GHz) + Doppler corporal

- **Thread scan:** captura CH 1/6/11 @ 2.4 GHz a cada 5s usando `hackrf_transfer`
- **Thread doppler:** 2.437 GHz @ 8 Hz; extrai BPM respiratório por FFT
- Adquire lock `'scan'` (timeout=3s) ou `'doppler'` (timeout=1s) antes de cada subprocess
- `pausar()` / `retomar()` para ceder ao IMSI e ao rádio FM

**Dados exportados no `/ws`:**
```
hackrf.canais["1"|"6"|"11"]: { potencia_dbm, variancia, historico[] }
hackrf.doppler: { presente, resp_bpm, confianca, variancia, n_amostras }
```

## ScannerEspectro (`spectrum_scanner.py`)

**Função:** Mapeamento FM (88–108 MHz) + wideband (108–900 MHz)

- FM: 150 kHz bins; wideband: 1 MHz bins
- Adquire lock UMA vez para FM+wideband (não libera entre os dois sweeps)
- Baseline adaptativo (~8 min para estabilizar, descarta outliers)
- Ciclo: ~24s total por sweep completo

**Dados exportados no `/ws`:**
```
espectro.freqs[]         ← em Hz
espectro.dbs[]
espectro.historico[20][] ← waterfall
espectro.fm[]: { freq_mhz, dbm, nome }         ← campo correto (NÃO fm_ativas)
espectro.anomalos[]: { freq_mhz, dbm, delta_db } ← campo correto (NÃO intelData.anomalos)
```

## ScannerInteligente (`intelligence_scanner.py`)

**Função:** Varredura completa 88–6000 MHz com categorização

- 1 sweep completo a cada ~12s (pode durar até 120s dependendo do hardware)
- Adquire lock por toda a duração do sweep
- Categorias: FM, CELULAR, WiFi-2G, 5G, ISM-2G, GNSS, DAB, SAT-MET, ISM-433, AERONAV, etc.

**Dados exportados no `/ws/intel`:**
```
intel.sinais[]: {
  freq_mhz,     ← JÁ em MHz, não dividir por 1e6
  dbm, dbm_max, delta_db,
  persistencia, ← fração de sweeps ativo (0–1)
  cat, cor, desc, novo
}
intel.panorama: { freqs[], dbm[], baseline[] }  ← freqs em MHz
intel.descobertos[80]: { freq_mhz, dbm, delta, cat, cor, desc, ts }
```

## ScannerIMSI (`imsi_scanner.py`)

**Função:** Interceptação GSM IMSI/TMSI via grgsm_livemon_headless

- Auto-scan: testa top-20 torres 15s cada; pula se não chegar pacote GSMTAP
- **Primeira ação:** `_pausar_todos_sensores()` — antes de escanear torres
- Lock `grgsm` adquirido antes do `Popen`, liberado após cada tentativa de frequência
- Recebe GSMTAP via UDP 4729; decodifica frames GSM e extrai IMSI/TMSI

**Dados exportados no `/ws/imsi`:**
```
torres[]: { banda, freq_mhz, arfcn, dbm, mcc, mnc, lac, cid, operadora, cor }
capturas[200]: { tipo, valor, mcc, mnc, operadora, cor, arfcn, dbm, ts }
freq_atual  ← frequência sendo testada no auto-scan (para exibir na UI)
```

## SensorAudio (`audio_sensor.py`)

**Função:** Microfone do Mac → detecção de respiração e batimentos cardíacos

- Usa CoreAudio via sounddevice/pyaudio
- Detecta AirPods se conectados (usa dispositivo mais sensível disponível)
- Exporta `audio.resp_audio.bpm` e `audio.card_audio.bpm` (fusão com dados RF no servidor)
