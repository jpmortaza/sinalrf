# HackRF One — Regras de Acesso

## Regra fundamental

O HackRF One é um dispositivo USB exclusivo: **apenas um processo pode abrir o dispositivo por vez.**
Qualquer acesso via `hackrf_transfer`, `hackrf_sweep` ou `grgsm` DEVE passar por `hackrf_resource.py`.

## API do hackrf_resource.py

```python
import hackrf_resource

# Adquirir lock antes de qualquer subprocess com HackRF
if not hackrf_resource.acquire('nome-do-modulo', timeout=5.0):
    return None  # HackRF ocupado — pula, tenta depois

try:
    subprocess.run(['hackrf_transfer', ...])
finally:
    hackrf_resource.release()

# Para atividade exclusiva (IMSI, radio FM, TX):
hackrf_resource.zerar()   # mata TODOS os processos hackrf ativos e reseta o lock
```

## Prioridade de acesso (mais alto = trava tudo abaixo)

| Módulo | Função | Timeout do lock |
|--------|--------|-----------------|
| `grgsm` (IMSI) | GSM decode exclusivo | minutos — trava tudo |
| `radio` (FM) | Streaming contínuo | minutos — trava tudo |
| `emergencia` | TX broadcast FM | até toda duração |
| `intel` | Sweep 88–6000 MHz | até 120s |
| `espectro` | Sweep 88–900 MHz | até 24s |
| `scan` | WiFi channels 1/6/11 | 300ms × 3 |
| `doppler` | Doppler 2.437 GHz | 100ms |

## Sequência correta para atividade exclusiva

```python
hackrf_resource.zerar()          # 1. mata processos, reseta lock
sensor_hackrf.pausar()           # 2. para loops internos de cada sensor
sensor_espectro.pausar()
sensor_intel.pausar()
hackrf_resource.acquire('grgsm') # 3. adquire lock
# ... faz a atividade exclusiva ...
hackrf_resource.release()        # 4. devolve
sensor_hackrf.retomar()          # 5. retoma sensores
```

## Diagnóstico rápido

| Sintoma | Causa provável | Fix |
|---------|----------------|-----|
| `hackrf device not found` | Outro processo abriu o HackRF | `hackrf_resource.zerar()` |
| IMSI captura e para | grgsm crashou | Ver `grgsm.log` |
| IMSI 0 capturas após 15s | Frequência sem GSM (LTE dominante) | Auto-scan tenta próxima torre |
| FM radio não inicia | HackRF em uso | Para IMSI primeiro |

## grgsm — detalhes críticos

- **Shebang obrigatório:** `#!/opt/homebrew/bin/python3` — esse Python tem gnuradio instalado
- **Gain correto:** LNA=40, VGA=40 (não usar gain padrão 10dB que o upstream hardcoda)
- **Porta interna:** `--serverport=4730` (server escuta GSMTAP em 4729 — portas diferentes)
- **SoapySDR:** usa `gnuradio.soapy` com `driver=hackrf` — NÃO osmosdr
- **Log:** `grgsm.log` na pasta do projeto

## Contexto GSM no Brasil (2026)

- TIM: desligou GSM-850/1900 em março 2023
- Claro: desligou GSM-850 em dezembro 2023
- Vivo: ainda pode ter GSM-900 em algumas áreas do interior
- Para verificar: usar `grgsm_scanner_fixed.py` — só encontra se houver BCCH GSM real
