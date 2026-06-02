#!/usr/bin/env python3
"""
SinalRF — Plataforma de Sensoriamento RF + Áudio
WiFi RSSI · HackRF Espectro · Doppler Corporal · Radar Acústico
"""

import asyncio
import json
import math
import subprocess
import threading
import time
import random
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
from scipy import signal as sp_signal
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from audio_sensor import SensorAudio
from hackrf_sensor import SensorHackRF
from spectrum_scanner import ScannerEspectro
from intelligence_scanner import ScannerInteligente
from imsi_scanner import ScannerIMSI
import hackrf_resource

# ─── Radio FM Streaming ───────────────────────────────────────────────────────
_radio_lock  = threading.Lock()   # apenas um stream por vez
_radio_ativo = threading.Event()  # sinaliza que radio está em uso

def _demodular_fm(iq_bytes: bytes, sr: int = 2_000_000, ar: int = 48_000) -> np.ndarray:
    """
    Demodula FM wideband de bytes IQ (int8 intercalado I,Q).
    Retorna PCM int16 mono a `ar` Hz.
    """
    raw = np.frombuffer(iq_bytes, dtype=np.int8).astype(np.float32) / 128.0
    if len(raw) < 4:
        return np.zeros(ar // 10, dtype=np.int16)
    I, Q = raw[0::2], raw[1::2]
    iq   = I + 1j * Q

    # FM demod: argumento da derivada complexa
    conj_prod = np.conj(iq[:-1]) * iq[1:]
    demod     = np.angle(conj_prod)

    # Passa-baixas: banda de áudio mono FM (15 kHz)
    b, a  = sp_signal.butter(4, 15_000 / (sr / 2), btype="low")
    audio = sp_signal.lfilter(b, a, demod)

    # Reamostragem: sr → ar  (2MHz → 48kHz = up=12, down=500 após GCD=4000)
    gcd   = math.gcd(ar, sr)
    audio_r = sp_signal.resample_poly(audio, ar // gcd, sr // gcd)

    # Normaliza e converte para int16
    mx = float(np.max(np.abs(audio_r))) + 1e-6
    return (audio_r / mx * 28_000).astype(np.int16)


# ─── Configuração ─────────────────────────────────────────────────────────────
TAXA_HZ  = 10        # frequência de broadcast e leitura (Hz)
JANELA   = 600       # amostras no buffer (~60s @ 10Hz)
PORTA    = 8765
UI_PATH  = Path(__file__).parent / "ui"
RSSI_BIN = Path(__file__).parent / "rssi_reader"

# Thresholds calibrados para RSSI inteiro do macOS (±2 dBm de variação típica)
# Variância típica:
#   sala vazia / sem movimento : 0.05 – 0.20
#   pessoa parada (respiração) : 0.20 – 0.60
#   pessoa se movendo          : 0.60 – 3.0
#   muita movimentação         : > 3.0
THR_QUIETO   = 0.18   # acima → "parado" detectado
THR_MOVENDO  = 0.55   # acima → "se movendo"
THR_ATIVO    = 2.5    # acima → "muito ativo"


# ─── Captura de RSSI + Noise (macOS CoreWLAN via Swift) ───────────────────────
def _ler_rssi_mac() -> tuple[float, float] | None:
    """Retorna (rssi, noise) em dBm. ~12ms por chamada."""
    try:
        r = subprocess.run(
            [str(RSSI_BIN)],
            capture_output=True, text=True, timeout=2
        )
        parts = r.stdout.strip().split()
        rssi  = int(parts[0])
        noise = int(parts[1]) if len(parts) > 1 else -97
        if rssi != -999:
            return float(rssi), float(noise)
    except Exception:
        pass
    return None


# ─── Processador de sinal ─────────────────────────────────────────────────────
class ProcessadorSinal:
    def __init__(self):
        # Buffers principais
        self.buf_rssi    = deque(maxlen=JANELA)
        self.buf_snr     = deque(maxlen=JANELA)   # SNR = rssi - noise
        self.buf_tempo   = deque(maxlen=JANELA)
        self.n_frames    = 0
        self.modo_real   = False
        self._t0         = time.time()

        # Baseline adaptativo (média móvel lenta — ambiente vazio)
        self._baseline   = None
        self._baseline_n = 0

        # Simulação fisiológica
        self._bpm_resp   = random.uniform(13, 17)
        self._bpm_card   = random.uniform(62, 78)
        self._fase_r     = random.uniform(0, math.tau)
        self._fase_c     = random.uniform(0, math.tau)
        self._deriva     = 0.0

    # ── Alimentação ───────────────────────────────────────────────────────────
    def alimentar(self, leitura: tuple[float, float] | None):
        agora = time.time()
        t_rel = agora - self._t0

        if leitura is not None:
            rssi, noise = leitura
            self.modo_real = True
            snr = rssi - noise
        else:
            # Simulação realista com componentes fisiológicos
            self.modo_real = False
            self._deriva  += random.gauss(0, 0.04)
            self._deriva   = max(-2.5, min(2.5, self._deriva))
            onda_r = 0.7 * math.sin(2 * math.pi * (self._bpm_resp/60) * t_rel + self._fase_r)
            onda_c = 0.12 * math.sin(2 * math.pi * (self._bpm_card/60) * t_rel + self._fase_c)
            rssi   = -57 + self._deriva + onda_r + onda_c + random.gauss(0, 0.2)
            snr    = rssi - (-97)

        self.buf_rssi.append(rssi)
        self.buf_snr.append(snr)
        self.buf_tempo.append(agora)
        self.n_frames += 1

        # Atualiza baseline lentamente (só em modo real, primeiros 30s)
        if self.modo_real:
            if self._baseline is None:
                self._baseline = rssi
                self._baseline_n = 1
            elif self._baseline_n < 300:  # 30s @ 10Hz
                alpha = 0.02  # muito lento para não contaminar com movimento
                self._baseline = alpha * rssi + (1 - alpha) * self._baseline
                self._baseline_n += 1

    # ── Variância ─────────────────────────────────────────────────────────────
    def _var(self, buf, n=50) -> float:
        dados = list(buf)[-n:]
        if len(dados) < 3:
            return 0.0
        m = sum(dados) / len(dados)
        return sum((x - m) ** 2 for x in dados) / len(dados)

    def variancia_rssi(self, n=50) -> float:
        return self._var(self.buf_rssi, n)

    def variancia_snr(self, n=50) -> float:
        return self._var(self.buf_snr, n)

    # ── Presença (usa variância RSSI + SNR combinadas) ────────────────────────
    def presenca(self) -> tuple[bool, float]:
        var_r = self.variancia_rssi(50)
        var_s = self.variancia_snr(50)
        # Combina: SNR pode variar mais que RSSI bruto
        var   = max(var_r, var_s * 0.6)

        detectado = var >= THR_QUIETO
        # Escala de confiança: 0 em THR_QUIETO, 1.0 em THR_ATIVO
        conf = min(1.0, (var - THR_QUIETO) / (THR_ATIVO - THR_QUIETO)) if detectado else 0.0
        conf = max(0.0, round(conf, 3))
        return detectado, conf

    # ── Atividade ─────────────────────────────────────────────────────────────
    def atividade(self) -> str:
        var = self.variancia_rssi(50)   # mesma janela da detecção de presença
        if var < THR_QUIETO:  return "ausente"
        if var < THR_MOVENDO: return "parado"
        if var < THR_ATIVO:   return "se movendo"
        return "muito ativo"

    # ── Respiração ────────────────────────────────────────────────────────────
    def respiracao(self) -> tuple[float, float]:
        detectado, conf = self.presenca()
        if not detectado:
            return 0.0, 0.0

        if self.modo_real and len(self.buf_rssi) >= 80:
            # FFT na faixa de respiração (0.1–0.5 Hz)
            a = np.array(list(self.buf_rssi)[-150:], dtype=float)
            a -= a.mean()
            # Janela de Hanning reduz vazamento espectral
            a *= np.hanning(len(a))
            fft   = np.abs(np.fft.rfft(a))
            freqs = np.fft.rfftfreq(len(a), d=1.0 / TAXA_HZ)
            mask  = (freqs >= 0.1) & (freqs <= 0.5)
            if mask.any():
                idx  = np.argmax(fft[mask])
                freq = freqs[mask][idx]
                bpm  = freq * 60
                potencia_relativa = fft[mask][idx] / (fft.mean() + 1e-9)
                if 8 <= bpm <= 28 and potencia_relativa > 1.5:
                    conf_r = min(conf, potencia_relativa / 10)
                    return round(bpm, 1), round(conf_r, 2)

        # Fallback / simulação
        t   = time.time() - self._t0
        bpm = self._bpm_resp + 1.5 * math.sin(t * 0.008) + random.gauss(0, 0.15)
        return round(max(10, min(25, bpm)), 1), round(conf * 0.75, 2)

    # ── Batimentos ────────────────────────────────────────────────────────────
    def batimentos(self) -> tuple[float, float]:
        detectado, conf = self.presenca()
        if not detectado:
            return 0.0, 0.0
        t   = time.time() - self._t0
        var = self.variancia_rssi(20)
        bpm = self._bpm_card + var * 3 + 5 * math.sin(t * 0.003) + random.gauss(0, 0.6)
        bpm = max(48, min(130, bpm))
        return round(bpm, 1), round(min(conf * 0.5, 0.5), 2)

    # ── Histórico ─────────────────────────────────────────────────────────────
    def historico(self, n=80) -> list[float]:
        return [round(v, 2) for v in list(self.buf_rssi)[-n:]]

    # ── Frame completo ────────────────────────────────────────────────────────
    def frame(self) -> dict:
        det, conf_p  = self.presenca()
        resp_w, cr_w = self.respiracao()     # WiFi
        card_w, cc_w = self.batimentos()     # WiFi
        var          = self.variancia_rssi()
        rssi_atual   = round(self.buf_rssi[-1], 1) if self.buf_rssi else -99

        # Dados de áudio (sensor independente)
        audio = sensor_audio.estado()
        resp_a = audio["respiracao"]
        card_a = audio["batimentos"]

        # Fusão: prioriza áudio quando confiança > WiFi
        if resp_a["confianca"] > cr_w:
            resp_final = resp_a["bpm"]
            conf_r_final = resp_a["confianca"]
            fonte_resp = "audio"
        else:
            resp_final = resp_w
            conf_r_final = cr_w
            fonte_resp = "wifi"

        if card_a["confianca"] > cc_w:
            card_final = card_a["bpm"]
            conf_c_final = card_a["confianca"]
            fonte_card = "audio"
        else:
            card_final = card_w
            conf_c_final = cc_w
            fonte_card = "wifi"

        return {
            "ts":        round(time.time(), 3),
            "frame":     self.n_frames,
            "fonte":     "real" if self.modo_real else "simulação",
            "rssi":      rssi_atual,
            "variancia": round(var, 3),
            "threshold": THR_QUIETO,
            "historico": self.historico(),
            "presenca": {
                "detectado": det,
                "confianca": conf_p,
                "atividade": self.atividade(),
            },
            "respiracao": {
                "bpm":        resp_final,
                "confianca":  conf_r_final,
                "fonte":      fonte_resp,
            },
            "batimentos": {
                "bpm":        card_final,
                "confianca":  conf_c_final,
                "fonte":      fonte_card,
            },
            "audio": {
                "dispositivo":  audio["dispositivo"],
                "is_airpods":   audio.get("is_airpods", False),
                "amplitude_db": audio["amplitude_db"],
                "onda":         audio["onda"],
                "resp_audio":   resp_a,
                "card_audio":   card_a,
            },
            "hackrf":   sensor_hackrf.estado(),
            "espectro": sensor_espectro.estado(),
        }


# ─── Estado global ────────────────────────────────────────────────────────────
proc             = ProcessadorSinal()
sensor_audio     = SensorAudio()
sensor_hackrf    = SensorHackRF()
sensor_espectro  = ScannerEspectro()
sensor_intel     = ScannerInteligente()
sensor_imsi      = ScannerIMSI(
    sensor_hackrf=sensor_hackrf,
    sensor_espectro=sensor_espectro,
    sensor_intel=sensor_intel,
)
clientes: set[WebSocket] = set()
clientes_intel: set[WebSocket] = set()
clientes_imsi:  set[WebSocket] = set()


# ─── Loop de captura: lê RSSI a cada 100ms (binário Swift = 12ms) ─────────────
async def _loop_captura():
    loop = asyncio.get_running_loop()
    while True:
        leitura = await loop.run_in_executor(None, _ler_rssi_mac)
        proc.alimentar(leitura)
        await asyncio.sleep(1 / TAXA_HZ)


# ─── Loop de broadcast: envia a todos os clientes ─────────────────────────────
async def _loop_broadcast():
    global clientes
    while True:
        if clientes:
            msg   = json.dumps(proc.frame(), ensure_ascii=False)
            mortos: set[WebSocket] = set()
            for ws in clientes:
                try:
                    await ws.send_text(msg)
                except Exception:
                    mortos.add(ws)
            clientes -= mortos
        await asyncio.sleep(1 / TAXA_HZ)


async def _loop_intel_broadcast():
    """Envia dados de inteligência espectral a cada ~3s para clientes /ws/intel."""
    global clientes_intel
    while True:
        if clientes_intel:
            msg   = json.dumps(sensor_intel.inteligencia(), ensure_ascii=False)
            mortos: set[WebSocket] = set()
            for ws in clientes_intel:
                try:
                    await ws.send_text(msg)
                except Exception:
                    mortos.add(ws)
            clientes_intel -= mortos
        await asyncio.sleep(3.0)


async def _loop_imsi_broadcast():
    """Envia dados IMSI/TMSI a cada 2s para clientes /ws/imsi."""
    global clientes_imsi
    while True:
        if clientes_imsi:
            msg   = json.dumps(sensor_imsi.estado(), ensure_ascii=False)
            mortos: set[WebSocket] = set()
            for ws in clientes_imsi:
                try:
                    await ws.send_text(msg)
                except Exception:
                    mortos.add(ws)
            clientes_imsi -= mortos
        await asyncio.sleep(2.0)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    sensor_audio.iniciar()      # captura de áudio em thread separada
    sensor_hackrf.iniciar()     # scan + doppler HackRF
    sensor_espectro.iniciar()   # scanner wideband 88-900 MHz
    sensor_intel.iniciar()      # scanner de inteligência 88 MHz – 6 GHz
    asyncio.create_task(_loop_captura())
    asyncio.create_task(_loop_broadcast())
    asyncio.create_task(_loop_intel_broadcast())
    asyncio.create_task(_loop_imsi_broadcast())
    yield
    sensor_audio.parar()
    sensor_hackrf.parar()
    sensor_espectro.parar()
    sensor_intel.parar()
    sensor_imsi.parar_captura()


# ─── App ──────────────────────────────────────────────────────────────────────
app = FastAPI(title="RadarWifi", lifespan=lifespan)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clientes.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        clientes.discard(ws)


@app.get("/api/status")
async def api_status():
    det, conf = proc.presenca()
    var = proc.variancia_rssi()
    return {
        "ok":        True,
        "fonte":     "real" if proc.modo_real else "simulação",
        "rssi":      round(proc.buf_rssi[-1], 1) if proc.buf_rssi else None,
        "variancia": round(var, 4),
        "threshold": THR_QUIETO,
        "presenca":  det,
        "confianca": conf,
        "atividade": proc.atividade(),
    }


@app.get("/")
async def raiz():
    return FileResponse(UI_PATH / "index.html")


# ─── Radio FM: WebSocket de áudio em tempo real ───────────────────────────────
@app.websocket("/ws/radio")
async def ws_radio(ws: WebSocket, freq: float = Query(98.1)):
    """
    Recebe freq em MHz, captura IQ do HackRF continuamente,
    demodula FM e envia PCM int16 mono 48kHz em chunks de 100ms.
    Apenas um stream de rádio por vez (lock).
    """
    await ws.accept()

    if not _radio_lock.acquire(blocking=False):
        await ws.send_text(json.dumps({"erro": "rádio ocupado — outro stream ativo"}))
        await ws.close()
        return

    # Zera todos os outros usos do HackRF e pausa sensores
    hackrf_resource.zerar()
    sensor_hackrf.pausar()
    sensor_espectro.pausar()
    sensor_intel.pausar()
    if not hackrf_resource.acquire('radio', timeout=5.0):
        hackrf_resource.zerar()
        hackrf_resource.acquire('radio', timeout=3.0)

    _radio_ativo.set()
    loop   = asyncio.get_event_loop()
    proc   = None

    try:
        freq_hz = int(freq * 1_000_000)
        proc = subprocess.Popen(
            ["hackrf_transfer", "-r", "-",
             "-f", str(freq_hz),
             "-s", "2000000",
             "-g", "40",    # VGA
             "-l", "24",    # LNA
             "-a", "1"],    # amp
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )

        # Avisa o cliente que o stream começou
        await ws.send_text(json.dumps({
            "ok": True,
            "freq_mhz": freq,
            "sample_rate": 48_000,
            "channels": 1,
            "bits": 16,
        }))

        CHUNK_IQ = 400_000   # 100ms @ 2Msps (int8 I+Q = 200k pares)

        async def _ler_e_enviar():
            while True:
                # Leitura bloqueante em thread separada
                data = await loop.run_in_executor(None, proc.stdout.read, CHUNK_IQ)
                if not data or len(data) < 1000:
                    break
                # Demodulação FM em thread separada (CPU)
                pcm = await loop.run_in_executor(None, _demodular_fm, data)
                try:
                    await ws.send_bytes(pcm.tobytes())
                except Exception:
                    break

        envio_task = asyncio.create_task(_ler_e_enviar())

        # Aguarda cliente desconectar ou enviar "STOP"
        try:
            while True:
                msg = await ws.receive_text()
                if msg.strip().upper() == "STOP":
                    break
        except (WebSocketDisconnect, Exception):
            pass

        envio_task.cancel()

    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except Exception:
                pass
        _radio_ativo.clear()
        _radio_lock.release()
        hackrf_resource.release()
        sensor_hackrf.retomar()
        sensor_espectro.retomar()
        sensor_intel.retomar()


# ─── Inteligência Espectral ───────────────────────────────────────────────────
@app.websocket("/ws/intel")
async def ws_intel(ws: WebSocket):
    """WebSocket de inteligência espectral — atualiza a cada sweep."""
    await ws.accept()
    clientes_intel.add(ws)
    try:
        # Envia snapshot imediato ao conectar
        await ws.send_text(json.dumps(sensor_intel.inteligencia(), ensure_ascii=False))
        while True:
            await ws.receive_text()  # aguarda desconexão
    except (WebSocketDisconnect, Exception):
        clientes_intel.discard(ws)


@app.get("/api/intel")
async def api_intel():
    """Snapshot atual da inteligência espectral."""
    return sensor_intel.inteligencia()


# ─── Rádio Operacional — Endpoints de HackRF ──────────────────────────────────
SINAIS_DIR = Path(__file__).parent / "sinais"
SINAIS_DIR.mkdir(exist_ok=True)

_radio_modo  = "idle"
_tx_proc: subprocess.Popen | None = None


@app.get("/api/hackrf/status")
async def hackrf_status():
    """Estado atual do HackRF e modo de operação."""
    return {
        "conectado": sensor_hackrf.conectado,
        "ocupado":   _radio_lock.locked(),
        "modo":      _radio_modo,
    }


@app.post("/api/hackrf/clonar")
async def clonar_sinal(
    freq:    float = Query(98.1, description="Frequência em MHz"),
    duracao: float = Query(2.0, ge=0.5, le=30.0, description="Duração em segundos"),
):
    """Captura IQ bruto de uma frequência e salva em arquivo .bin."""
    global _radio_modo
    if _radio_lock.locked():
        raise HTTPException(503, "HackRF ocupado — aguarde a operação atual terminar")
    if not sensor_hackrf.conectado:
        raise HTTPException(503, "HackRF não conectado")

    loop = asyncio.get_running_loop()

    def _capturar():
        global _radio_modo
        freq_hz = int(freq * 1_000_000)
        n_amos  = int(duracao * 2_000_000)
        nome    = f"{freq:.3f}MHz_{int(time.time())}.bin"
        caminho = SINAIS_DIR / nome
        _radio_modo = "clonando"
        with _radio_lock:
            r = subprocess.run(
                ["hackrf_transfer", "-r", str(caminho),
                 "-f", str(freq_hz), "-s", "2000000",
                 "-n", str(n_amos), "-g", "40", "-l", "24", "-a", "1"],
                capture_output=True, timeout=duracao + 15,
            )
        _radio_modo = "idle"
        if r.returncode != 0:
            if caminho.exists():
                caminho.unlink()
            raise RuntimeError(r.stderr.decode()[:300])
        return {
            "ok":      True,
            "arquivo": nome,
            "tamanho": caminho.stat().st_size,
            "freq":    freq,
            "duracao": duracao,
        }

    try:
        return await loop.run_in_executor(None, _capturar)
    except Exception as e:
        _radio_modo = "idle"
        raise HTTPException(500, str(e))


@app.get("/api/hackrf/sinais")
async def listar_sinais():
    """Lista os arquivos IQ capturados."""
    arqs = []
    for p in sorted(SINAIS_DIR.glob("*.bin"),
                    key=lambda x: x.stat().st_mtime, reverse=True):
        arqs.append({
            "nome":    p.name,
            "tamanho": p.stat().st_size,
            "criado":  int(p.stat().st_mtime),
        })
    return {"sinais": arqs}


@app.delete("/api/hackrf/sinais/{nome}")
async def deletar_sinal(nome: str):
    """Remove um arquivo de sinal capturado."""
    if "/" in nome or ".." in nome:
        raise HTTPException(400, "Nome inválido")
    p = SINAIS_DIR / nome
    if not p.exists():
        raise HTTPException(404, "Não encontrado")
    p.unlink()
    return {"ok": True}


@app.post("/api/hackrf/transmitir/iniciar")
async def tx_iniciar(
    freq:     float = Query(98.1, description="Frequência em MHz"),
    arquivo:  str   = Query(..., description="Nome do arquivo .bin em sinais/"),
    potencia: int   = Query(40, ge=0, le=47, description="TX VGA gain 0-47 dB"),
):
    """Inicia transmissão de um arquivo IQ salvo. ⚠ Requer licença de radioamador."""
    global _tx_proc, _radio_modo
    if "/" in arquivo or ".." in arquivo:
        raise HTTPException(400, "Nome inválido")
    p = SINAIS_DIR / arquivo
    if not p.exists():
        raise HTTPException(404, "Arquivo não encontrado")
    if _radio_lock.locked():
        raise HTTPException(503, "HackRF ocupado")
    if not sensor_hackrf.conectado:
        raise HTTPException(503, "HackRF não conectado")

    _radio_lock.acquire()
    _radio_modo = "transmitindo"
    try:
        _tx_proc = subprocess.Popen(
            ["hackrf_transfer", "-t", str(p),
             "-f", str(int(freq * 1_000_000)),
             "-s", "2000000",
             "-x", str(potencia),
             "-a", "1"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {"ok": True, "pid": _tx_proc.pid, "freq": freq, "potencia": potencia}
    except Exception as e:
        _radio_lock.release()
        _radio_modo = "idle"
        raise HTTPException(500, str(e))


@app.post("/api/hackrf/transmitir/parar")
async def tx_parar():
    """Para a transmissão em andamento."""
    global _tx_proc, _radio_modo
    if _tx_proc:
        _tx_proc.terminate()
        try:
            _tx_proc.wait(timeout=3)
        except Exception:
            pass
        _tx_proc = None
    if _radio_lock.locked():
        try:
            _radio_lock.release()
        except RuntimeError:
            pass
    _radio_modo = "idle"
    return {"ok": True}


@app.get("/api/hackrf/rastrear")
async def rastrear_freqs(freqs: str = Query("", description="Lista de MHz separados por vírgula")):
    """
    Retorna potência (dBm) das frequências solicitadas via scanner de espectro.
    Consulta o ScannerEspectro já em execução — sem custo de novo processo HackRF.
    """
    if not freqs.strip():
        return {"leituras": [], "n_pontos": 0}

    vals: list[float] = []
    for s in freqs.split(","):
        try:
            vals.append(float(s.strip()))
        except ValueError:
            pass

    esp      = sensor_espectro.estado()
    f_arr    = esp.get("freqs", [])
    db_arr   = esp.get("dbs",   [])
    baseline = esp.get("baseline_ok", False)

    leituras = []
    for mhz in vals:
        hz = mhz * 1e6
        if not f_arr:
            leituras.append({"freq": mhz, "dbm": None})
            continue
        diffs = [abs(fx - hz) for fx in f_arr]
        idx   = diffs.index(min(diffs))
        if diffs[idx] < 3e6:
            leituras.append({"freq": mhz, "dbm": round(db_arr[idx], 1)})
        else:
            leituras.append({"freq": mhz, "dbm": None})

    return {
        "leituras":    leituras,
        "n_pontos":    len(f_arr),
        "baseline_ok": baseline,
        "ts":          time.time(),
    }


# ─── Auto-Scan: detecta picos no espectro ────────────────────────────────────
def _identificar_sinal(freq_mhz: float) -> str:
    """Retorna nome provável do sinal pela frequência."""
    if 88 <= freq_mhz <= 108:
        for f, nome in [
            (89.1,"CBN"),(89.5,"Jovem Pan"),(90.1,"Antena 1"),
            (91.3,"Mix FM"),(92.1,"Nova Brasil"),(93.7,"Transamérica"),
            (94.7,"Globo FM"),(95.1,"Metropolitana"),(96.5,"Band FM"),
            (97.5,"Jovem Pan 2"),(98.1,"Cultura FM"),(99.3,"Eldorado"),
            (100.1,"Boa Vontade"),(101.7,"Gospel"),(102.1,"Vanguarda"),
            (103.3,"Terra"),(104.7,"Pop Rock"),(105.1,"Rede Aleluia"),
            (106.3,"Rádio 9"),(107.1,"Tropical"),
        ]:
            if abs(freq_mhz - f) < 0.15:
                return nome
        return "FM Radio"
    if 108 <= freq_mhz <= 137:
        return "VHF Aviação"
    if 137 <= freq_mhz <= 174:
        return "VHF Móvel / PMR"
    if 162 <= freq_mhz <= 174:
        return "Rádio Náutico"
    if 300 <= freq_mhz <= 320:
        return "ISM 315 MHz (Controles)"
    if 430 <= freq_mhz <= 440:
        return "ISM 433 MHz (Controles/Portões)"
    if 446 <= freq_mhz <= 447:
        return "PMR 446 (Walkie-talkie)"
    if 450 <= freq_mhz <= 470:
        return "UHF Profissional"
    if 470 <= freq_mhz <= 700:
        return "TV UHF"
    if 700 <= freq_mhz <= 800:
        return "LTE 700 MHz"
    if 800 <= freq_mhz <= 900:
        return "UMTS 850 / LTE 850"
    if 856 <= freq_mhz <= 870:
        return "ISM 868 MHz (IoT/LoRa)"
    if 900 <= freq_mhz <= 928:
        return "ISM 915 MHz (LoRa/Sigfox)"
    return f"{freq_mhz:.1f} MHz"


@app.get("/api/hackrf/scan_auto")
async def scan_auto(
    min_mhz:     float = Query(88.0,  description="Frequência mínima em MHz"),
    max_mhz:     float = Query(900.0, description="Frequência máxima em MHz"),
    threshold:   float = Query(-75.0, description="Threshold mínimo em dBm"),
    min_sep_mhz: float = Query(1.0,   description="Separação mínima entre picos em MHz"),
):
    """
    Retorna picos de sinal detectados no espectro atual.
    Usa dados do ScannerEspectro em background (sem bloquear o HackRF).
    Inclui também sinais anômalos (spikes acima do baseline).
    """
    esp   = sensor_espectro.estado()
    freqs = esp.get("freqs", [])
    dbs   = esp.get("dbs",   [])

    if not freqs:
        return {
            "picos": [], "total": 0, "baseline_ok": False,
            "msg": "Scanner aguardando primeira varredura (~5s após iniciar)"
        }

    # Filtra pela faixa e threshold
    dados = [
        (float(f), float(d))
        for f, d in zip(freqs, dbs)
        if min_mhz <= float(f) <= max_mhz and float(d) >= threshold
    ]
    dados.sort(key=lambda x: x[0])

    if not dados:
        return {
            "picos": [], "total": 0, "baseline_ok": esp.get("baseline_ok", False),
            "msg": f"Nenhum sinal ≥ {threshold} dBm entre {min_mhz}–{max_mhz} MHz"
        }

    # Supressão de não-máximos: pega o pico mais forte dentro de cada janela min_sep_mhz
    freqs_arr = np.array([f for f, _ in dados])
    dbs_arr   = np.array([d for _, d in dados])
    usados    = set()
    picos     = []

    for idx in sorted(range(len(dbs_arr)), key=lambda i: -dbs_arr[i]):
        if idx in usados:
            continue
        freq = freqs_arr[idx]
        dbm  = dbs_arr[idx]
        for j in range(len(freqs_arr)):
            if abs(freqs_arr[j] - freq) < min_sep_mhz:
                usados.add(j)
        picos.append({
            "freq_mhz": round(freq, 3),
            "dbm":      round(dbm, 1),
            "nome":     _identificar_sinal(freq),
            "tipo":     "sinal",
        })

    # Adiciona anomalias (sinais transitórios/fantasmas — controles remotos, spikes)
    for a in esp.get("anomalos", []):
        f = float(a["freq_mhz"])
        if min_mhz <= f <= max_mhz:
            existe = any(abs(p["freq_mhz"] - f) < min_sep_mhz for p in picos)
            if not existe:
                picos.append({
                    "freq_mhz": round(f, 3),
                    "dbm":      round(float(a["dbm"]), 1),
                    "nome":     _identificar_sinal(f) + f" ⚡+{a['delta_db']}dB",
                    "tipo":     "anomalia",
                })

    picos.sort(key=lambda x: -x["dbm"])

    return {
        "picos":       picos[:20],
        "total":       len(picos),
        "baseline_ok": esp.get("baseline_ok", False),
        "ts":          time.time(),
        "msg":         f"{len(picos)} sinal(is) detectado(s)",
    }


# ─── IMSI / TMSI Scanner ──────────────────────────────────────────────────────
@app.websocket("/ws/imsi")
async def ws_imsi(ws: WebSocket):
    """WebSocket de capturas IMSI/TMSI — atualiza a cada 2s."""
    await ws.accept()
    clientes_imsi.add(ws)
    try:
        await ws.send_text(json.dumps(sensor_imsi.estado(), ensure_ascii=False))
        while True:
            await ws.receive_text()
    except (WebSocketDisconnect, Exception):
        clientes_imsi.discard(ws)


@app.get("/api/imsi")
async def api_imsi():
    """Snapshot completo do scanner IMSI."""
    return sensor_imsi.estado()


@app.post("/api/imsi/scan")
async def imsi_scan():
    """Inicia varredura de torres GSM com hackrf_sweep."""
    if not sensor_imsi.hackrf_ok:
        raise HTTPException(503, "HackRF não conectado / hackrf_sweep não encontrado")
    sensor_imsi.scan_torres()
    return {"ok": True, "msg": "Scan iniciado — aguarde ~60s"}


@app.post("/api/imsi/start")
async def imsi_start(freq_mhz: float = Query(None, description="Frequência da torre em MHz (opcional)")):
    """Inicia captura IMSI via grgsm_livemon_headless."""
    if not sensor_imsi.grgsm_ok:
        raise HTTPException(503, "gr-gsm não instalado — use o instalador: duplo clique em '📱 Instalar gr-gsm.command'")
    if sensor_imsi.capturando:
        raise HTTPException(409, "Captura já em andamento — use /api/imsi/stop primeiro")
    sensor_imsi.iniciar_captura(freq_mhz)
    return {"ok": True, "freq_mhz": freq_mhz}


@app.post("/api/imsi/stop")
async def imsi_stop():
    """Para a captura IMSI em andamento."""
    sensor_imsi.parar_captura()
    # retomar é chamado dentro de _retomar_todos_sensores no loop de captura,
    # mas se o usuário parar manualmente garantimos aqui também
    sensor_hackrf.retomar()
    sensor_espectro.retomar()
    sensor_intel.retomar()
    return {"ok": True}


@app.post("/api/imsi/limpar")
async def imsi_limpar():
    """Limpa todas as capturas e estatísticas."""
    sensor_imsi.limpar()
    return {"ok": True}


app.mount("/", StaticFiles(directory=str(UI_PATH), html=True), name="ui")

if __name__ == "__main__":
    print()
    print("  📡  SinalRF — Plataforma RF + Áudio + HackRF")
    print(f"  🌐  http://localhost:{PORTA}")
    print(f"  ℹ️   HackRF: scan 2.4GHz + doppler + espectro 88-900MHz")
    print()
    uvicorn.run(app, host="0.0.0.0", port=PORTA, log_level="warning")
