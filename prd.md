# PRD — mtzRF

## O que é

Plataforma de sensoriamento RF em tempo real para um único operador com HackRF One.
Monitora presença humana, sinais de rádio, celular e WiFi; inclui rádio FM, interceptação GSM e broadcast de emergência.

## Problema que resolve

Concentrar em uma interface web local todas as capacidades do HackRF One: espectro, presença por Doppler, rádio FM, IMSI/GSM, ambiente RF 3D e análise por IA local — sem depender de cloud.

## Usuários

Jean Mortaza — operador único. Uso em campo e em laboratório.

## Funcionalidades implementadas

- [x] Dashboard em tempo real (WiFi RSSI, Doppler, presença, respiração, batimentos)
- [x] Rádio FM (WFM/NFM/AM) com nomes de estações brasileiras
- [x] Scanner de espectro 88–6000 MHz com categorização e histórico
- [x] Ambiente RF 3D (canvas 2D perspectiva) com pilares, esferas, partículas
- [x] Doppler WiFi passivo + análise por LLM local
- [x] Interceptação GSM/IMSI (grgsm_fixed.py, auto-scan 20 torres)
- [x] Integração LLM local (Ollama / qwen2.5:3b) com contexto RF
- [x] Broadcast de emergência climática: TTS → FM modulate → TX multi-frequência
- [x] Design system unificado (themes.css + nav.js) com tema preto e neutro
- [x] hackrf_resource.py — lock global exclusivo do HackRF

## Fora de escopo (por enquanto)

- Multi-usuário / rede (plataforma local single-user)
- TX além de FM (spread spectrum, jamming, etc.)
- Interface mobile

## Métricas de sucesso

- HackRF nunca trava por acesso concorrente
- Latência WS < 100ms para dados principais
- IMSI captura em frequências GSM ativas (Vivo 900 MHz)
