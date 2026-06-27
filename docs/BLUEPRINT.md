# Blueprint — mtzRF como sistema completo unificado

> Plano de arquitetura para transformar o mtzRF de uma **coleção de ~25 telas + ~22 módulos
> Python** que disputam um único HackRF, em **UM sistema coeso** de inteligência de RF e
> contra‑vigilância defensiva. Cobre os quatro eixos pedidos: **TSCM / inteligência de RF**,
> **contra‑vigilância de ambiente (varredura física guiada)**, **monitoramento 24/7** e
> **pentest WiFi/rede defensivo**.
>
> Documento de planejamento — verificado contra o código atual. Não é registro de implementação.

---

## 1. Visão

Hoje cada página é uma ferramenta isolada e o HackRF é arbitrado por um `threading.Lock`
cego (`hackrf_resource.py`), que mata processos por **nome de imagem** (`taskkill /IM`), sem
device nem prioridade. Estado vive disperso e volátil: `historico/` com JSON soltos (sem
operador/local/autorização/assinatura), `baselines/*.npz`, e alertas/baseline de rede só em
memória (`agendador._alertas`, `_prev`, `sentinela._cand`) — **somem no restart**. Não existe
SQLite no projeto.

O sistema unificado é governado por **três peças novas que costuram tudo**:

1. **RadioBroker** — arbitra o HackRF (e futuros SDRs) por **prioridade / lease / TTL**, no
   lugar do Lock cego. Mata por **PID/serial**, não por nome global.
2. **EstadoSistema** — **fonte única de verdade** (perfil ativo, dono do SDR, devices, saúde,
   alertas) lida por toda a UI e API.
3. **Sessão/Caso em SQLite** (`store.py` / `mtzrf.db`) — dá **proveniência, trending e laudo
   assinado** a tudo que os sensores capturam.

Sobre essa base existem **Perfis de Operação** (Idle, Varredura/TSCM, Sentinela 24/7,
Varredura de Ambiente com laudo, Pentest WiFi/Rede) que ligam conjuntos coerentes de módulos
com **um clique** e conduzem o operador passo a passo — em **campo** (tablet) ou **laboratório**
(desktop).

**Limite firme, por design:** tudo recepção/defensivo. Sem deauth, jamming, GPS spoofing nem
evil‑twin de roubo de senha. TX só legal/autorizado. Captura de credencial só em portal de
conscientização autorizado. Esses caminhos **não existem no código** — guardrails no
Broker/perfil recusam qualquer tarefa TX não autorizada.

---

## 2. Arquitetura em camadas

```
8) UI POR MODO          seletor de modo · Painel de Missão · densidade Campo/Lab · dono do SDR · banner legal
7) API / WS UNIFICADOS  WSHub genérico · envelope {topico,ts,dados,saude} · /ws/sistema
6) PERSISTÊNCIA & LAUDO  store.py (SQLite WAL) · HMAC/cadeia de custódia · relatórios PDF
5) ORQUESTRAÇÃO          Perfis de Operação (JSON) · orquestrador 24/7 · tscm_pipeline · varredura.py · threat_score
4) MOTORES (sensores)    spectrum/intel/tscm/tscm_video/sentinela/burst/sonda/gps/adsb/ism/net/wifi/audio/imsi/fm
3) ESTADO & EVENTOS      EstadoSistema · barramento de alertas (alertas.py) · health_monitor (watchdog)
2) ARBITRAGEM DE RÁDIO   RadioBroker atrás da fachada acquire/release/zerar de hackrf_resource
1) HARDWARE & DEVICES    HackRF One · placas WiFi (recon) · RTL‑SDR (futuro, RX 24/7) · 802.11/IMSI via WSL2
```

**Regra física do HackRF:** half‑duplex, 1 canal → **contenção é a regra**. O Broker
**arbitra, nunca multiplexa**. Sensores contínuos (intel/espectro/scan‑doppler) recebem
janelas por **time‑slicing** com cota mínima garantida (anti‑starvation); tarefas exclusivas
(radio/IMSI/burst/vídeo/sonda) **preemptam** via `on_preempt → pausar() → zerar(serial) →
lock → retomar` ao soltar. **24/7 real só com um 2º SDR.**

---

## 3. Módulos do sistema

Legenda de status: **existe** (reaproveitar) · **parcial** (evoluir) · **novo** (criar).

### Núcleo de coesão
| Módulo | Papel | Status |
|---|---|---|
| `hackrf_resource.py` | Fachada `acquire/release/zerar/dono`; mantida como caso N=1 do Broker | existe |
| `radio_broker.py` (RadioBroker) | Prioridade/lease/TTL, time‑slicing, preempção, roteamento por device, kill por PID/serial | novo |
| `EstadoSistema` | Fonte única de verdade lida lock‑free por `/ws` e `/api` | novo |
| Perfis de Operação | Registro declarativo `{perfil:[módulos,prioridades,device]}`; transição atômica | parcial |
| `WSHub` + envelope versionado | Hub genérico de broadcast `{topico,ts,dados,saude}` + `/ws/sistema` | parcial |
| `health_monitor.py` (watchdog) | Probe `hackrf_info`, detecta USB/lock/zumbi, auto‑recupera; `/api/saude` | novo |

### Persistência e laudo
| Módulo | Papel | Status |
|---|---|---|
| `store.py` (SQLite `mtzrf.db`) | Sessões, varreduras, sinais, baselines, anomalias, alertas, capturas, evidências, `audit_log` | novo |
| Assinatura HMAC + cadeia de custódia | SHA‑256 por evidência + HMAC encadeado (chave via DPAPI); `/api/verificar` | novo |
| `historico.py` | CRUD + render de laudo; migra p/ `store` e ganha proveniência + rodapé assinado | parcial |
| Relatório PDF + export ZIP | PDF via Edge headless (fallback WeasyPrint/HTML); pacote autocontido c/ manifesto SHA‑256 | parcial |
| Retenção / ciclo de vida | Política por tipo (IQ 30d, agregados 90d, laudos 2a), `legal_hold`, purga auditada | novo |

### Inteligência de RF / TSCM
| Módulo | Papel | Status |
|---|---|---|
| `intelligence_scanner.py` | Sweep 88–6000 MHz; panorama + sinais classificados; fonte do baseline | existe |
| `spectrum_scanner.py` | Sweep FM 88–108 + wideband 108–900; baseline adaptativo | existe |
| `tscm_scanner.py` | Varre presets, classifica suspeita 0–3 (ESCUTA?/CAM‑VID/CELULAR); porta de entrada | parcial |
| `tscm_video.py` | Decode de vídeo analógico FM 1.2/2.4/5.8 GHz; sync = câmera confirmada | existe |
| `burst_hunter.py` | IQ banda larga + espectrograma; isola transmissores intermitentes (VOX/GSM) | existe |
| Sonda near‑field (`/ws/nearfield`) | RSSI ao vivo + tom quente/frio p/ localizar fisicamente o emissor | existe |
| `voice_confirm.py` | Demodula candidato, VAD 300–3400 Hz, cross‑corr **microfone↔RF** = confirmador de escuta | novo |
| `threat_score.py` | Funde features (banda/BW, novo‑vs‑baseline, duty, voz, sync vídeo, RSSI) → score 0–100 explicável | novo |
| `tscm_pipeline.py` | Máquina de estados do caso: detectar→classificar→confirmar→localizar→registrar | novo |

### Monitoramento 24/7
| Módulo | Papel | Status |
|---|---|---|
| `sentinela.py` | Baseline `.npz` por local + anomalia + monitor; candidatos hoje só em memória | parcial |
| `telemetria.py` + baseline percentil | Séries em SQLite; **p50/p95/p99 por (bin,hora)** substituindo o MAX por bin; trending/drift | novo |
| Orquestrador de monitoramento | Agenda tarefas‑HackRF por intervalo/janela horária respeitando o Broker | novo |
| `alertas.py` (barramento) | Normaliza RF+rede+WiFi, severidade INFO/AVISO/ALERTA/CRÍTICO, dedupe+cooldown+quiet‑hours | novo |
| `agendador.py` | Loop background WiFi/rede + alertas; baseline/alertas hoje só em memória | parcial |

### Contra‑vigilância de ambiente
| Módulo | Papel | Status |
|---|---|---|
| `varredura.py` (sessão de campo) | Orquestra a varredura física guiada passo‑a‑passo; agrega achados; calcula risco; dispara laudo | novo |
| Checklist de inspeção física | Roteiro manual não‑RF (tomadas, espelhos, lente‑finder/IR) com nota/foto por item | novo |

### Pentest WiFi / rede (defensivo)
| Módulo | Papel | Status |
|---|---|---|
| `net_scanner.py` | Ping‑sweep + ARP + portas RTSP/ONVIF + OUI; descobre câmeras IP na /24 | existe |
| `wifi_tools.py` | Recon netsh + heurística rogue/evil‑twin + cofre do portal (arm/disarm, captura gated) | existe |
| `inventario_rede.py` (asset store) | Inventário persistente APs/hosts com first/last_seen, whitelist, **diff temporal real** | novo |
| `hotspot_portal.py` | Mobile Hotspot (WinRT) + DNS/redirect local p/ portal de conscientização autorizado | novo |
| Sensor 802.11 WSL2/ESP32 | Captura real (SSID oculto, deauth‑rate, evil‑twin signature) — **read‑only, nunca injeta** | novo |
| `imsi_scanner.py` (WSL2/grgsm) | GSM IMSI/TMSI; precisa relay GSMTAP WSL2→Windows + lock cross‑OS | parcial |

### UI
| Módulo | Papel | Status |
|---|---|---|
| `themes.css` | Tokens + 2 temas; falta escala de densidade Campo/Lab e alvos de toque ≥44px | existe |
| `nav.js` | Menu lateral agrupado + START/STOP; falta seletor de MODO e indicador global de dono do SDR | parcial |
| Painel de Missão + `modos.js` | Stepper que embute páginas existentes como blocos; veredito por passo | novo |
| Perfil Campo vs Laboratório | Toggle `data-densidade` (localStorage): campo = 1 número + 1 veredito; lab = multi‑painel | novo |

---

## 4. Fluxos de operação

### A. Varredura TSCM (pipeline detectar→localizar→laudo, HackRF sequencial)
1. Abre um **CASO** (local + escopo); `tscm_pipeline` cria sessão no `store` (proveniência completa).
2. **Detectar:** `tscm_scanner` por banda (lock por banda via Broker) + consulta baseline `.npz`
   do local → candidatos `{freq,bw,dbm,modo,novo}`.
3. **Classificar:** categoria + suspeita base; `threat_score` dá pontos iniciais; fila por score.
4. **Confirmar‑temporal:** nos top‑N, lease exclusivo → `burst_hunter` captura IQ ~2 s; duty
   intermitente/VOX soma, contínuo largo perde (broadcast/WiFi).
5. **Confirmar‑conteúdo (áudio):** `voice_confirm` demodula + VAD; o "teste de fala"
   cruza‑correlaciona **microfone↔envelope RF** — correlação alta = escuta ao vivo (maior peso).
6. **Confirmar‑conteúdo (vídeo):** se CAM‑VID, `tscm_video` tenta decode; sync+imagem = câmera
   analógica confirmada; só ruído = "possível câmera digital/cifrada, não decodificável".
7. **Localizar:** `/ws/nearfield` na freq do candidato; operador caminha pelo tom quente/frio.
8. **Registrar:** `store` grava candidatos/scores/evidências; `historico` gera laudo.

### B. Sentinela 24/7 (monitoramento persistente desacompanhado)
1. **Setup:** escolhe local, janela e tarefas (intel contínuo + burst 10 min + gps 5 min +
   WiFi/rede 5 min) e janelas horárias; config persistida.
2. **Baseline percentil:** `telemetria` acumula por (bin,hora) p50/p95/p99 — capta ritmo
   circadiano e reduz falso‑positivo (substitui o MAX por bin atual).
3. **Time‑slicing:** orquestrador escolhe a próxima tarefa‑HackRF elegível, pausa as demais,
   adquire lease, roda, libera; tarefas sem SDR (WiFi/rede) correm em paralelo.
4. **Detecção:** compara com o percentil da **hora atual** + margem por banda; anomalia precisa
   persistir N sweeps e é gravada em disco (sobrevive a restart).
5. **Alerta+roteamento:** `alertas.py` atribui severidade (jamming GPS = CRÍTICO, vídeo novo =
   ALERTA), dedupe+cooldown, grava no `store`, empurra p/ `/ws/sistema` e SMS se ≥ALERTA.
6. **Desacompanhado:** watchdog verifica heartbeats; congelou > TTL → `zerar()`+retomar;
   USB sumiu → polling até reaparecer e reinicializa.
7. **Recuperação:** após reboot, Task Scheduler reinicia `server.py`, lê `estado_sessao.json`,
   retoma a sessão e registra o **gap de cobertura**.
8. **Relatório de sessão:** uptime/cobertura %, gaps, timeline de anomalias, mapa de calor por hora.

### C. Varredura de ambiente em campo (wizard com laudo assinado)
0. **Escopo:** cria sessão (cliente, local, operador, escopo **autorizado c/ consentimento**);
   sem consentimento, captura de portal fica bloqueada.
1–2. **Silenciar + baseline do local:** mapeia emissores conhecidos; `sentinela.aprender` salva
   `baselines/<local>.npz`.
3–4. **Varredura por banda + comparar:** `tscm_scanner` percorre presets; `sentinela.comparar`
   destaca o **novo** vs RF normal do local.
5. **Aprofundar suspeitos:** `burst_hunter.cacar(freq)` + `tscm_video.analisar(freq)`.
6. **Sonda near‑field:** caminha até o pico de RSSI; anota a posição física.
7. **Câmeras de rede e WiFi:** `net_scanner` lista câmeras IP; `wifi_tools` flagra AP plantado.
8–9. **Inspeção física + consolidação:** checklist item a item; `varredura.py` calcula risco.
10–11. **Laudo + monitoramento:** congela a sessão, gera laudo HTML/PDF (caso, risco, seção por
   passo, conclusão, assinatura, **hash SHA‑256 + cadeia de custódia**); oferece ligar
   sentinela 24/7 ancorada no baseline do local.

### D. Pentest WiFi & rede defensivo (não depende do HackRF)
1. **Baseline:** `wifi_tools` + `net_scanner` gravam no `inventario_rede` com `first_seen`;
   operador marca APs/hosts confiáveis.
2. **Monitoramento contínuo:** diff contra o inventário **persistido** → só alerta deltas reais.
3. **Rogue/evil‑twin:** heurística CRUZA inventário + energia RF (`hackrf_sensor`) + sensor WSL2
   opcional → alerta multi‑camada, não só string.
4. **Câmeras IP guiado:** banner‑grab leve (RTSP/HTTP) confirma modelo e **aponta** risco
   (nunca testa login); liga ao eixo TSCM (câmera IP + vídeo analógico = duas vias).
5. **Portal de conscientização autorizado:** dupla salvaguarda → hotspot rotulado + DNS local →
   submissão gated → **tela de revelação educativa** → métricas. Nunca clone de rede alheia.
6. **Relatório de postura:** inventário + delta + alertas + evidência RF correlacionada.

### E. Troca de perfil com conflito de HackRF (transversal)
Indicador global lê `hackrf_resource.dono()`; ao trocar de modo abre diálogo
("Rádio FM está usando o HackRF — parar e iniciar varredura?"); confirma →
`Broker.preempt_all()` → `EstadoSistema` atualizado → `/ws/sistema` notifica **todas** as
páginas abertas; ao sair do modo exclusivo, o perfil anterior é retomado.

---

## 5. Dados e relatórios

**Alvo — `store.py` / `mtzrf.db`** (SQLite + WAL + `busy_timeout` + fila de escrita serializada
para não dar "database is locked" com os threads daemon). Tabelas: `operadores`, `locais`,
`autorizacoes`, `sessoes`, `varreduras`, `sinais`, `baselines` (indexa o `.npz` + checksum),
`anomalias`, `alertas`, `capturas`, `evidencias`, `telemetria` (agregados por bin,hora),
`audit_log` (hash encadeado).

- **Proveniência:** toda evidência herda operador + local + autorização + hardware (serial via
  `hackrf_info`) + **timestamp UTC ISO‑8601** (hoje grava horário local string — ambíguo em perícia).
- **Integridade:** payload JSON canônico → SHA‑256 por evidência; HMAC‑SHA256 por laudo (chave
  via **DPAPI**); hash encadeado no `audit_log` detecta edição/remoção. *Honestidade:* HMAC local
  prova integridade, **não** não‑repúdio forte.
- **Baselines:** cálculo numpy intocado, só indexado/versionado p/ medir drift. Evolui de MAX
  por bin → **percentil p95/p99 por (bin,hora)**.
- **Relatórios:** `historico.py` vira render. HTML imprimível (já existe) + **PDF real** via
  Edge/Chromium headless (fallback WeasyPrint/HTML). Tipos: caso‑TSCM, laudo de ambiente,
  relatório de sessão de vigilância, relatório de postura WiFi/rede.
- **Export:** `/api/export/{sessao}` monta **ZIP autocontido** (PDF+HTML+JSON+IQ/áudio +
  `manifesto.json` com SHA‑256). `/api/verificar` recomputa hashes e confere HMAC + cadeia.
- **Migração não‑destrutiva:** importador varre `historico/*.json` e `baselines/*.npz`, insere
  marcados como "legado/sem assinatura".

---

## 6. UI por modo

Um único design system (`themes.css`, 2 temas) + camada de orquestração por cima,
**reaproveitando as páginas existentes como blocos** (não reescrever).

- **Navegação por MODO:** promover "modo" de conceito de hardware a conceito de UX. Seletor no
  topo reorganiza o menu lateral filtrando só as ferramentas do modo (reduz a carga de ~25
  ferramentas sempre visíveis). No celular, bottom‑tab de modos.
- **Painel de Missão:** cartão persistente que conduz por N passos por modo; cada passo só libera
  o próximo quando tem veredito. *Risco:* iframes podem duplicar WebSockets e disputar o HackRF →
  exige WSHub único.
- **Densidade Campo vs Lab:** toggle `data-densidade` no `<html>`. **Campo** (toque): 1 número
  grande + 1 veredito, alvos ≥44px, feedback háptico; **Lab**: multi‑painel, waterfall, export.
- **Indicador global de dono do HackRF:** resolve o erro nº1 (device ocupado) na própria UX.
- **Salvaguardas na UI:** banner permanente de limite legal nos modos WiFi/TX; portal exige dupla
  autorização + watermark; IMSI/Emergência TX só em modo Avançado.
- **Realidade Windows/móvel:** em campo o tablet acessa via `http://IP:8765` (HTTP simples) —
  Vibration API e fullscreen têm restrições fora de HTTPS/gesto; testar no navegador alvo.

---

## 7. Roadmap

| Fase | Objetivo | Esforço |
|---|---|---|
| **0 — Portabilidade Windows** | Rodar nativo e confiável antes de refatorar | baixo |
| **1 — MVP coeso** | Estado central + perfis + persistência + laudo (sem multi‑SDR/IMSI) | alto |
| **2 — RadioBroker + Sentinela 24/7** | Arbitragem cooperativa + monitoramento desacompanhado | alto |
| **3 — Pipeline TSCM avançado** | Confirmação por conteúdo (voz/vídeo) + score explicável | médio |
| **4 — Multi‑SDR (RTL‑SDR p/ 24/7)** | Liberar o HackRF do gargalo half‑duplex | médio |
| **5 — IMSI integrado via WSL2** | GSM na UI com lock cross‑OS (maior risco) | alto |

**Fase 0 — Portabilidade Windows (pré‑requisito)**
- Fixar toolchain HackRF no PATH (PothosSDR/hackrf‑tools) + driver WinUSB via Zadig; validar
  `hackrf_sweep/transfer/info`.
- Remover *drift* macOS do `CLAUDE.md`/`base-conhecimento` (`/opt/homebrew`, shebangs brew).
- Validar os 6 WebSockets e ~40 endpoints no Windows; corrigir `zerar()` p/ rastrear **PIDs**
  dos subprocess (hoje mata por nome de imagem `.exe`).

**Fase 1 — MVP coeso (~80–95% reaproveitamento)**
- WSHub + envelope versionado + `EstadoSistema` + `/ws/sistema`.
- Perfis de Operação declarativos (substitui o `if/elif` de `hackrf_start()`); default seguro IDLE/RX.
- `store.py` (SQLite WAL) + migração não‑destrutiva dos JSON/npz legados.
- Proveniência + assinatura SHA‑256/HMAC + `audit_log` encadeado.
- `varredura.py` (wizard de campo) reaproveitando 100% dos motores + laudo assinado.
- Barramento de alertas unificado + `inventario_rede` persistente (diff real WiFi/rede).
- UI por modo: seletor + Painel de Missão + densidade Campo/Lab + indicador de dono + banner legal.
- Guardrails legais centralizados.

**Fase 2 — RadioBroker + Sentinela 24/7**
- RadioBroker atrás da fachada (fila/lease/TTL/time‑slicing/preempção); rotear 100% dos acessos.
- `health_monitor` (watchdog) + `/api/saude` + badge.
- `telemetria` + baseline percentil; anomalias persistidas.
- Orquestrador (janelas/intervalos) + recuperação (Task Scheduler, `estado_sessao.json`, gap) +
  retenção + relatório de sessão.
- SMS crítico via Twilio com throttle/quiet‑hours.

**Fase 3 — Pipeline TSCM avançado**
- `threat_score` explicável + "candidato canônico" que acumula features.
- `tscm_pipeline` (máquina de estados) + `/ws/tscm/caso` + fila por score.
- `voice_confirm` (cross‑corr microfone↔RF).
- Fan‑out automático: `burst_hunter` + `tscm_video` nos top‑N.
- Unificar baseline (pipeline consome o `.npz` por‑local da sentinela).

**Fase 4 — Multi‑SDR**
- Device registry + `zerar()` por serial/PID (**pré‑requisito** do 2º SDR).
- Broker roteia por capacidade: 24/7 → RTL‑SDR, HackRF livre p/ TSCM/near‑field/burst.
- Perfis de antena por banda + UI "monte esta antena".
- Sensor 802.11 WSL2/ESP32 (read‑only).

**Fase 5 — IMSI integrado via WSL2**
- Lock cross‑OS WSL2↔Windows (named mutex/arquivo de sinal).
- Relay UDP GSMTAP WSL2→Windows + `usbipd attach` automático.
- Gate legal reforçado (autorização registrada no `store`, default bloqueado).

---

## 8. Limites legais e honestidade do laudo

- **Não‑negociável:** sem deauth, jamming, GPS spoofing, evil‑twin de roubo de senha. TX só
  legal/autorizado — guardrails no Broker/perfil recusam tarefa TX não autorizada.
- **Portal de conscientização:** só com autorização explícita (dupla salvaguarda), watermark
  "isto é um teste", tela de revelação educativa obrigatória, trilha de auditoria.
- **Mobile Hotspot/captive portal:** só hotspot **próprio rotulado** para exercício autorizado,
  nunca clone de rede alheia. Frágil no Windows (sem DNS captive nativo).
- **IMSI/GSM:** restrições fortes no Brasil (Anatel / Lei de Interceptação) mesmo do próprio SIM;
  default defensivo, autorização registrada antes de liberar. WSL2‑only.
- **TX FM / qualquer transmissão:** restrições Anatel; gate de autorização + log antes de liberar;
  default bloqueado fora do modo lab autorizado.
- **Honestidade do laudo (anti‑falsa sensação de segurança):** declarar o **escopo coberto e o
  que NÃO foi verificado** — câmeras digitais/WiFi cifradas não são decodificáveis (`tscm_video`
  só pega analógicas), `net_scanner` só vê a rede conectada, bugs sem transmissão (gravador local)
  ou com fio/laser e sinais sub‑80 MHz / >6 GHz não são detectáveis por RF. O checklist físico é
  defesa parcial.
- **Classificação sempre "possível/provável"**, nunca categórica — `tscm_scanner` é heurístico;
  FM/celular/WiFi do prédio podem pontuar alto. Mitigar com baseline por‑local + confirmação.
- **Cadeia de custódia é dissuasiva, não absoluta:** HMAC local não impede quem controla a
  máquina+chave. Comunicar o nível de garantia.
- **Privacidade:** inventário (MACs), IMSI e credenciais de portal em SQLite **sem criptografia
  em repouso** — retenção curta, marcação "dado de teste", backup protegido.
- **Default seguro:** após crash/reboot, sempre **IDLE/RX** a menos que reconfirmado (perfil
  persistido não deve reabrir em TX/exclusivo).
