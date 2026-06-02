"""
mtzHRF — LLM Local Client (Ollama / LM Studio)
================================================
Integra o LLM local com os dados RF em tempo real.

Dois modos:
  1. EMBED  — usa nomic-embed-text (já instalado) para classificar sinais
              por similaridade semântica com descrições conhecidas
  2. CHAT   — usa modelo de chat (llama3.2, qwen2.5, etc.) para análise
              em linguagem natural do ambiente RF

Para ativar o modo CHAT instale um modelo leve:
  ollama pull llama3.2:3b       ← 2 GB, rápido
  ollama pull qwen2.5:3b        ← 1.9 GB, muito bom em português
  ollama pull phi4-mini:latest  ← 3.8 GB, mais capaz
"""

import json
import time
import threading
from typing import Optional, Any
from collections import deque

try:
    import httpx
    _HTTP_OK = True
except ImportError:
    import urllib.request, urllib.error
    _HTTP_OK = False

# ── Configuração ───────────────────────────────────────────────────────────────
OLLAMA_BASE = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text:latest"   # sempre disponível
CHAT_MODEL  = None                         # detectado automaticamente

# Base de conhecimento RF — usada para classificação por embedding
_RF_KNOWLEDGE = [
    {"tag": "FM_BROADCAST",  "text": "FM radio broadcast station 88-108 MHz wideband FM music voice"},
    {"tag": "AVIATION_VHF",  "text": "aviation VHF AM voice tower approach control ATIS 118-137 MHz aircraft"},
    {"tag": "MARINE_VHF",    "text": "marine VHF NFM channel 16 156-174 MHz boat ship coastal harbor emergency"},
    {"tag": "LTE_4G_850",    "text": "LTE 4G cellular mobile network 850 MHz band 5 downlink OFDM digital"},
    {"tag": "LTE_4G_2100",   "text": "LTE 4G cellular AWS 2100 MHz band 1 4 downlink OFDM digital encrypted"},
    {"tag": "LTE_5G",        "text": "5G NR new radio cellular 3500 MHz millimeter wave OFDM digital"},
    {"tag": "WIFI_2G",       "text": "WiFi 802.11 2.4 GHz ISM OFDM channel 1 6 11 DSSS spread spectrum"},
    {"tag": "WIFI_5G",       "text": "WiFi 802.11ac 5 GHz ISM OFDM channel 36 40 44 48 5.8 GHz"},
    {"tag": "GPS_GNSS",      "text": "GPS GNSS satellite navigation L1 1575 MHz spread spectrum BPSK C/A code"},
    {"tag": "ISM_433",       "text": "ISM 433 MHz remote control OOK ASK sensor weather station garage door"},
    {"tag": "PMR_UHF",       "text": "PMR446 UHF professional mobile radio NFM 446 MHz walkie-talkie security"},
    {"tag": "DAB_DIGITAL",   "text": "DAB digital audio broadcast 174-230 MHz OFDM digital radio"},
    {"tag": "PAGER",         "text": "pager POCSAG FLEX 148 MHz 169 MHz 466 MHz digital on-call"},
    {"tag": "ADS_B",         "text": "ADS-B aircraft transponder 1090 MHz Mode-S position altitude"},
    {"tag": "ACARS",         "text": "ACARS aircraft communications 129 MHz 136 MHz digital messages"},
    {"tag": "WEATHER_SAT",   "text": "weather satellite NOAA APT 137 MHz meteorological image"},
    {"tag": "TRUNKING",      "text": "P25 DMR trunked radio public safety police fire EMS UHF VHF"},
    {"tag": "RADAR",         "text": "radar pulse doppler weather surveillance L band S band range"},
]

_kb_embeddings: list | None = None   # cache dos embeddings da base de conhecimento


# ── Detecção automática de modelos ─────────────────────────────────────────────

def detectar_modelos() -> dict:
    """Retorna dicionário com modelos disponíveis no Ollama."""
    try:
        r = _get(f"{OLLAMA_BASE}/api/tags")
        modelos = [m["name"] for m in r.get("models", [])]
        chat = _escolher_chat(modelos)
        return {"embed": EMBED_MODEL, "chat": chat, "todos": modelos}
    except Exception:
        return {"embed": None, "chat": None, "todos": []}


def _escolher_chat(modelos: list[str]) -> Optional[str]:
    """Prefere modelos menores e mais rápidos em português."""
    preferencia = ["qwen2.5", "llama3.2", "llama3.1", "phi4", "phi3",
                   "gemma2", "mistral", "deepseek", "llama2", "llama3"]
    for pref in preferencia:
        for m in modelos:
            if pref in m.lower() and "embed" not in m.lower():
                return m
    # Qualquer modelo que não seja embed
    for m in modelos:
        if "embed" not in m.lower() and "nomic" not in m.lower():
            return m
    return None


# ── HTTP helpers ───────────────────────────────────────────────────────────────

def _get(url: str, timeout: float = 5.0) -> dict:
    if _HTTP_OK:
        r = httpx.get(url, timeout=timeout)
        return r.json()
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def _post(url: str, data: dict, timeout: float = 30.0) -> dict:
    payload = json.dumps(data).encode()
    if _HTTP_OK:
        r = httpx.post(url, content=payload,
                       headers={"Content-Type": "application/json"},
                       timeout=timeout)
        return r.json()
    req = urllib.request.Request(url, data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _post_stream(url: str, data: dict, timeout: float = 60.0):
    """Generator que retorna chunks do stream de chat."""
    payload = json.dumps({**data, "stream": True}).encode()
    if _HTTP_OK:
        with httpx.stream("POST", url, content=payload,
                          headers={"Content-Type": "application/json"},
                          timeout=timeout) as r:
            for line in r.iter_lines():
                if line:
                    try:
                        chunk = json.loads(line)
                        if chunk.get("message", {}).get("content"):
                            yield chunk["message"]["content"]
                        if chunk.get("done"):
                            break
                    except Exception:
                        pass
    else:
        req = urllib.request.Request(url, data=payload,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            for line in r:
                line = line.decode().strip()
                if line:
                    try:
                        chunk = json.loads(line)
                        if chunk.get("message", {}).get("content"):
                            yield chunk["message"]["content"]
                        if chunk.get("done"):
                            break
                    except Exception:
                        pass


# ── Embeddings ─────────────────────────────────────────────────────────────────

def embed(texto: str) -> list[float] | None:
    """Gera embedding com nomic-embed-text."""
    try:
        r = _post(f"{OLLAMA_BASE}/api/embeddings",
                  {"model": EMBED_MODEL, "prompt": texto},
                  timeout=10.0)
        return r.get("embedding")
    except Exception:
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    import math
    dot = sum(x * y for x, y in zip(a, b))
    na  = math.sqrt(sum(x * x for x in a))
    nb  = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb + 1e-9)


def _carregar_kb():
    """Pré-calcula embeddings da base de conhecimento RF (faz uma vez)."""
    global _kb_embeddings
    if _kb_embeddings is not None:
        return
    _kb_embeddings = []
    for item in _RF_KNOWLEDGE:
        emb = embed(item["text"])
        if emb:
            _kb_embeddings.append({"tag": item["tag"], "text": item["text"], "emb": emb})


def classificar_sinal(freq_mhz: float, dbm: float, descricao: str = "") -> dict:
    """
    Classifica um sinal por similaridade de embedding com a base de conhecimento.
    Retorna: { tag, similaridade, texto_match }
    """
    try:
        _carregar_kb()
        if not _kb_embeddings:
            return {}
        query = f"signal at {freq_mhz} MHz strength {dbm} dBm {descricao}"
        q_emb = embed(query)
        if not q_emb:
            return {}
        melhor = max(_kb_embeddings, key=lambda x: _cosine(q_emb, x["emb"]))
        sim = _cosine(q_emb, melhor["emb"])
        return {
            "tag":          melhor["tag"],
            "similaridade": round(sim, 3),
            "texto":        melhor["text"],
        }
    except Exception:
        return {}


# ── Chat ───────────────────────────────────────────────────────────────────────

_SYSTEM_RF = """Você é um assistente especialista em Radio Frequência (RF) e SDR (Software Defined Radio).
Você tem acesso aos dados em tempo real do receptor HackRF One do usuário.
Responda em português brasileiro, de forma técnica mas acessível.
Seja conciso — máximo 3 parágrafos por resposta, a menos que seja solicitado mais detalhe.
Nunca invente dados — se não tiver informação suficiente, diga claramente."""


def chat(mensagem: str, contexto_rf: dict | None = None,
         modelo: str | None = None,
         historico: list | None = None) -> str:
    """
    Envia mensagem para o LLM com contexto RF atual.
    Retorna resposta completa como string.
    """
    mdl = modelo or _escolher_chat(detectar_modelos().get("todos", []))
    if not mdl:
        return ("⚠ Nenhum modelo de chat instalado. "
                "Execute: ollama pull qwen2.5:3b")

    msgs = [{"role": "system", "content": _SYSTEM_RF}]

    # Injeta contexto RF
    if contexto_rf:
        ctx = _formatar_contexto(contexto_rf)
        msgs.append({"role": "system",
                     "content": f"DADOS RF EM TEMPO REAL:\n{ctx}"})

    # Histórico de conversa
    if historico:
        msgs.extend(historico[-10:])   # últimas 10 mensagens

    msgs.append({"role": "user", "content": mensagem})

    resposta_chunks = []
    for chunk in _post_stream(f"{OLLAMA_BASE}/api/chat",
                              {"model": mdl, "messages": msgs},
                              timeout=60.0):
        resposta_chunks.append(chunk)

    return "".join(resposta_chunks)


def chat_stream(mensagem: str, contexto_rf: dict | None = None,
                modelo: str | None = None,
                historico: list | None = None):
    """Versão streaming — generator de chunks de texto."""
    mdl = modelo or _escolher_chat(detectar_modelos().get("todos", []))
    if not mdl:
        yield "⚠ Instale um modelo: ollama pull qwen2.5:3b"
        return

    msgs = [{"role": "system", "content": _SYSTEM_RF}]
    if contexto_rf:
        msgs.append({"role": "system",
                     "content": f"DADOS RF EM TEMPO REAL:\n{_formatar_contexto(contexto_rf)}"})
    if historico:
        msgs.extend(historico[-10:])
    msgs.append({"role": "user", "content": mensagem})

    yield from _post_stream(f"{OLLAMA_BASE}/api/chat",
                            {"model": mdl, "messages": msgs},
                            timeout=60.0)


def _formatar_contexto(d: dict) -> str:
    """Formata dados RF para injetar como contexto no LLM."""
    linhas = []

    # Espectro
    esp = d.get("espectro", {})
    if esp.get("fm"):
        fm_list = ", ".join(f"{s['freq_mhz']} MHz ({s.get('nome','?')})"
                            for s in esp["fm"][:5])
        linhas.append(f"FM ativas: {fm_list}")
    if esp.get("anomalos"):
        gh = ", ".join(f"{s['freq_mhz']} MHz (+{s['delta_db']}dB)"
                       for s in esp["anomalos"][:4])
        linhas.append(f"Sinais fantasma: {gh}")

    # Intel
    sinais = d.get("sinais", [])[:8]
    if sinais:
        linhas.append("Sinais detectados (freq_mhz | dBm | categoria | persistência):")
        for s in sinais:
            linhas.append(f"  {s.get('freq_mhz')} MHz | {s.get('dbm')} dBm | "
                          f"{s.get('cat','?')} | {int((s.get('persistencia',0))*100)}%")

    # HackRF
    hrf = d.get("hackrf", {})
    dop = hrf.get("doppler", {})
    if dop.get("presente"):
        linhas.append(f"Doppler: PRESENÇA DETECTADA — resp {dop.get('resp_bpm',0)} bpm")

    # WiFi
    linhas.append(f"RSSI WiFi: {d.get('rssi','?')} dBm | Variância: {d.get('variancia','?')}")

    return "\n".join(linhas) if linhas else "Sem dados RF disponíveis"


# ── Análise periódica ──────────────────────────────────────────────────────────

class AnalisePeriodicaRF:
    """
    Roda análise LLM em background a cada N minutos.
    Acumula insights para exibir na UI.
    """
    def __init__(self, intervalo_s: int = 300):
        self.intervalo  = intervalo_s
        self.insights:  deque = deque(maxlen=20)
        self._parar     = threading.Event()
        self._thread    = None

    def iniciar(self, fonte_dados):
        """fonte_dados: callable que retorna dict com contexto RF atual."""
        self._fonte = fonte_dados
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="llm-analise"
        )
        self._thread.start()

    def parar(self):
        self._parar.set()

    def _loop(self):
        while not self._parar.is_set():
            self._parar.wait(self.intervalo)
            if self._parar.is_set():
                break
            try:
                ctx = self._fonte()
                mdl = _escolher_chat(detectar_modelos().get("todos", []))
                if not mdl:
                    continue
                resp = chat(
                    "Analise o ambiente RF atual. Destaque qualquer sinal incomum, "
                    "identifique as fontes principais e comente sobre variações. "
                    "Seja conciso (2 parágrafos).",
                    contexto_rf=ctx,
                    modelo=mdl,
                )
                self.insights.appendleft({
                    "ts":    time.time(),
                    "texto": resp,
                    "ctx_resumo": f"{len(ctx.get('sinais',[]))} sinais",
                })
            except Exception as e:
                self.insights.appendleft({
                    "ts":    time.time(),
                    "texto": f"Erro na análise: {e}",
                    "ctx_resumo": "—",
                })

    def get_insights(self) -> list:
        return list(self.insights)
