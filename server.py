#!/usr/bin/env python3
"""
mtzHRF — Plataforma de Sensoriamento RF + Áudio
WiFi RSSI · HackRF Espectro · Doppler Corporal · Radar Acústico
"""

import asyncio
import json
import math
import os
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
try:
    import llm_client
    _LLM_OK = True
except Exception:
    _LLM_OK = False

# ─── Radio FM Streaming ───────────────────────────────────────────────────────
_radio_lock  = threading.Lock()   # apenas um stream por vez
_radio_ativo = threading.Event()  # sinaliza que radio está em uso

# ─── FM Modulator ─────────────────────────────────────────────────────────────
def _tts_para_wav(texto: str, voz: str = "Luciana") -> bytes:
    """Usa macOS say + afconvert para gerar WAV mono 22050 Hz a partir de texto."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        aiff = os.path.join(tmp, "tts.aiff")
        wav  = os.path.join(tmp, "tts.wav")
        subprocess.run(["say", "-v", voz, "-o", aiff, "--", texto],
                       check=True, timeout=30, capture_output=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@22050",
                        aiff, wav], check=True, timeout=10, capture_output=True)
        with open(wav, "rb") as f:
            return f.read()


def _modular_fm_iq(wav_bytes: bytes,
                   sr_iq: int = 2_000_000,
                   deviation: int = 75_000) -> bytes:
    """
    Modula PCM WAV mono em FM wideband (WBFM) e retorna IQ int8 para hackrf_transfer.
    Pre-emphasis 50µs (padrão CCIR/Brasil). Desvio padrão 75 kHz.
    """
    import io, wave as _wave
    with _wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        sr_audio = wf.getframerate()
        n_ch     = wf.getnchannels()
        raw      = wf.readframes(wf.getnframes())

    pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if n_ch == 2:
        pcm = (pcm[0::2] + pcm[1::2]) / 2   # stereo → mono

    pcm = pcm / (np.max(np.abs(pcm)) + 1e-6) * 0.9   # normaliza com headroom

    # Resample para sr_iq
    gcd_val = math.gcd(sr_iq, sr_audio)
    audio   = sp_signal.resample_poly(pcm, sr_iq // gcd_val, sr_audio // gcd_val)

    # Pre-emphasis 50µs
    tau      = 50e-6
    bz, az   = sp_signal.bilinear([tau, 0], [tau, 1], fs=sr_iq)
    audio    = sp_signal.lfilter(bz, az, audio)

    # FM modulation: fase = integral cumulativa do áudio
    k    = 2 * np.pi * deviation / sr_iq
    fase = np.cumsum(audio * k)
    iq   = np.exp(1j * fase).astype(np.complex64)

    # Serializar I,Q int8 interleaved (formato hackrf_transfer)
    out      = np.zeros(len(iq) * 2, dtype=np.int8)
    out[0::2] = np.clip(iq.real * 120, -127, 127).astype(np.int8)
    out[1::2] = np.clip(iq.imag * 120, -127, 127).astype(np.int8)
    return out.tobytes()


# Estado do broadcast de emergência
_emergencia: dict = {
    "ativo":        False,
    "progresso":    "",
    "freq_atual":   None,
    "freq_total":   0,
    "freq_idx":     0,
    "iq_arquivo":   None,
    "_thread":      None,
    "_parar":       threading.Event(),
}


def _iq_from_bytes(iq_bytes: bytes) -> "np.ndarray":
    """Converte int8 interleaved I,Q do hackrf_transfer em array complexo normalizado."""
    raw = np.frombuffer(iq_bytes, dtype=np.int8).astype(np.float32) / 128.0
    return (raw[0::2] + 1j * raw[1::2])

def _normalizar_audio(audio: "np.ndarray", ar: int, amplitude: int = 28_000) -> "np.ndarray":
    """Normaliza float32 → int16 PCM."""
    mx = float(np.max(np.abs(audio))) + 1e-6
    return (audio / mx * amplitude).astype(np.int16)

def _resamplar(audio: "np.ndarray", sr: int, ar: int) -> "np.ndarray":
    g = math.gcd(ar, sr)
    return sp_signal.resample_poly(audio, ar // g, sr // g)


def _demodular_fm(iq_bytes: bytes, sr: int = 2_000_000, ar: int = 48_000) -> np.ndarray:
    """WFM — Wideband FM (rádio broadcast). Retorna PCM int16 mono."""
    iq = _iq_from_bytes(iq_bytes)
    if len(iq) < 4:
        return np.zeros(ar // 10, dtype=np.int16)
    # Discriminador FM por diferença de fase
    demod = np.angle(np.conj(iq[:-1]) * iq[1:])
    # Passa-baixas 15 kHz (banda de áudio FM mono)
    b, a = sp_signal.butter(4, 15_000 / (sr / 2), btype="low")
    audio = sp_signal.lfilter(b, a, demod)
    # De-ênfase 75 µs (padrão Americas)
    tau = 75e-6
    bz, az = sp_signal.bilinear([1], [tau, 1], fs=sr)
    audio = sp_signal.lfilter(bz, az, audio)
    return _normalizar_audio(_resamplar(audio, sr, ar), ar)


def _demodular_nfm(iq_bytes: bytes, sr: int = 2_000_000, ar: int = 8_000,
                   ch_bw: int = 12_500) -> np.ndarray:
    """NFM — Narrow FM (VHF marino, UHF profissional, segurança pública, FRS).
    ch_bw: largura do canal em Hz (12500 = padrão moderno, 25000 = legado).
    Retorna PCM int16 mono."""
    iq = _iq_from_bytes(iq_bytes)
    if len(iq) < 4:
        return np.zeros(ar // 10, dtype=np.int16)
    nyq = sr / 2
    # Isola canal NFM com lowpass (metade da largura do canal)
    b, a = sp_signal.butter(5, (ch_bw / 2) / nyq, btype="low")
    iq_f = sp_signal.lfilter(b, a, iq)
    # Discriminador FM
    demod = np.angle(np.conj(iq_f[:-1]) * iq_f[1:])
    # Passa-faixa de voz 300–3400 Hz
    b2, a2 = sp_signal.butter(4, [300 / nyq, 3400 / nyq], btype="band")
    audio = sp_signal.lfilter(b2, a2, demod)
    audio -= float(np.mean(audio))  # remove DC
    return _normalizar_audio(_resamplar(audio, sr, ar), ar)


def _demodular_am(iq_bytes: bytes, sr: int = 2_000_000, ar: int = 8_000,
                  ch_bw: int = 8_000) -> np.ndarray:
    """AM — Amplitude Modulation (aviação VHF 118–137 MHz, AM broadcast, militar VHF).
    ch_bw: largura do canal em Hz (8000 = aviação, 10000 = AM broadcast).
    Retorna PCM int16 mono."""
    iq = _iq_from_bytes(iq_bytes)
    if len(iq) < 4:
        return np.zeros(ar // 10, dtype=np.int16)
    nyq = sr / 2
    # Lowpass para isolar canal AM
    b, a = sp_signal.butter(5, (ch_bw / 2) / nyq, btype="low")
    iq_f = sp_signal.lfilter(b, a, iq)
    # Detecção de envelope (AM demod)
    envelope = np.abs(iq_f)
    # Remove portadora DC com highpass ~50 Hz
    b2, a2 = sp_signal.butter(3, 50 / nyq, btype="high")
    audio = sp_signal.lfilter(b2, a2, envelope)
    # Passa-faixa de voz 300–3400 Hz
    b3, a3 = sp_signal.butter(4, [300 / nyq, 3400 / nyq], btype="band")
    audio = sp_signal.lfilter(b3, a3, audio)
    return _normalizar_audio(_resamplar(audio, sr, ar), ar)


# Mapeamento categoria → modo de demodulação
_CAT_MODO = {
    "FM":         ("WFM", 48_000),
    "AERONAV":    ("AM",   8_000),
    "VHF-MIL":    ("AM",   8_000),
    "MARÍTIMO":   ("NFM",  8_000),
    "FRS":        ("NFM",  8_000),
    "VHF-UHF":    ("NFM",  8_000),
    "UHF-PROF":   ("NFM",  8_000),
    "DESCONHECIDO": ("NFM", 8_000),
}
# Categorias sem demodulação de áudio possível
_CAT_NO_AUDIO = {"CELULAR", "WiFi-2G", "5G", "ISM-2G", "GNSS", "DAB", "DAB-L",
                 "SAT-MET", "ISM-433"}

def _demodular(iq_bytes: bytes, mode: str = "WFM", sr: int = 2_000_000) -> np.ndarray:
    """Dispatcher multi-mode. mode: 'WFM' | 'NFM' | 'AM'."""
    if mode == "NFM":
        return _demodular_nfm(iq_bytes, sr=sr)
    if mode == "AM":
        return _demodular_am(iq_bytes, sr=sr)
    return _demodular_fm(iq_bytes, sr=sr)  # WFM default


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


# ─── Radio Multi-Mode: WebSocket de áudio em tempo real ─────────────────────
@app.websocket("/ws/radio")
async def ws_radio(ws: WebSocket,
                   freq: float = Query(98.1),
                   mode: str   = Query("WFM")):
    """
    Recebe freq em MHz + mode (WFM/NFM/AM), captura IQ do HackRF,
    demodula e envia PCM int16 mono em chunks de 100ms.
    mode: 'WFM' (FM broadcast), 'NFM' (VHF marino/UHF), 'AM' (aviação)
    Apenas um stream por vez (lock).
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
        # Taxa de áudio por modo
        _mode = mode.upper()
        if _mode not in ("WFM", "NFM", "AM"):
            _mode = "WFM"
        _ar_audio = 48_000 if _mode == "WFM" else 8_000

        await ws.send_text(json.dumps({
            "ok": True,
            "freq_mhz": freq,
            "mode": _mode,
            "sample_rate": _ar_audio,
            "channels": 1,
            "bits": 16,
        }))

        CHUNK_IQ = 400_000   # 100ms @ 2Msps (int8 I+Q = 200k pares)

        async def _ler_e_enviar():
            while True:
                data = await loop.run_in_executor(None, proc.stdout.read, CHUNK_IQ)
                if not data or len(data) < 1000:
                    break
                # Demodulação multi-mode em thread separada (CPU)
                pcm = await loop.run_in_executor(
                    None, _demodular, data, _mode, 2_000_000
                )
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


@app.get("/api/radio/modo")
async def radio_modo_por_categoria(cat: str = Query(...)):
    """
    Retorna o modo de demodulação e se tem áudio para a categoria dada.
    cat: categoria do sinal (ex: 'FM', 'AERONAV', 'CELULAR', ...)
    """
    if cat in _CAT_NO_AUDIO:
        return {"audio": False, "modo": None,
                "motivo": f"{cat} usa protocolo digital/criptografado — sem áudio"}
    modo, ar = _CAT_MODO.get(cat, ("NFM", 8_000))
    return {"audio": True, "modo": modo, "ar": ar}


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


# ─── LLM / IA Local ──────────────────────────────────────────────────────────

# ─── Emergência Climática — Broadcast Multi-Frequência ───────────────────────

@app.get("/api/emergencia/status")
async def emergencia_status():
    return {
        "ativo":      _emergencia["ativo"],
        "progresso":  _emergencia["progresso"],
        "freq_atual": _emergencia["freq_atual"],
        "freq_idx":   _emergencia["freq_idx"],
        "freq_total": _emergencia["freq_total"],
        "iq_pronto":  _emergencia["iq_arquivo"] is not None and os.path.exists(_emergencia["iq_arquivo"] or ""),
    }


@app.post("/api/emergencia/preparar")
async def emergencia_preparar(body: dict):
    """
    Prepara o arquivo IQ para broadcast de emergência via TTS.
    Body: { "texto": "...", "voz": "Luciana", "repeticoes": 2 }
    """
    texto = body.get("texto", "").strip()
    if not texto:
        raise HTTPException(400, "texto vazio")

    voz        = body.get("voz", "Luciana")
    repeticoes = max(1, min(10, int(body.get("repeticoes", 3))))

    # Gera WAV via TTS em executor (bloqueante)
    loop = asyncio.get_event_loop()
    try:
        wav_bytes = await loop.run_in_executor(None, _tts_para_wav, texto, voz)
    except Exception as e:
        raise HTTPException(500, f"TTS falhou: {e}")

    # Multiplica o áudio pelas repetições (com pausa de 1s entre elas)
    import io, wave as _wave
    with _wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        sr = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    silencio = b'\x00\x00' * sr   # 1s de silêncio

    pcm_total = (raw + silencio) * repeticoes
    buf = io.BytesIO()
    with _wave.open(buf, "wb") as wf2:
        wf2.setnchannels(1); wf2.setsampwidth(2); wf2.setframerate(sr)
        wf2.writeframes(pcm_total)
    wav_final = buf.getvalue()

    # Modula para IQ FM em executor
    try:
        iq_bytes = await loop.run_in_executor(None, _modular_fm_iq, wav_final)
    except Exception as e:
        raise HTTPException(500, f"Modulação FM falhou: {e}")

    # Salva IQ em arquivo temporário
    iq_path = str(Path(__file__).parent / "sinais" / "_emergencia.iq")
    with open(iq_path, "wb") as f:
        f.write(iq_bytes)

    _emergencia["iq_arquivo"] = iq_path
    duracao = len(iq_bytes) / (2 * 2_000_000)   # int8 I+Q @ 2Msps

    return {
        "ok":        True,
        "iq_bytes":  len(iq_bytes),
        "duracao_s": round(duracao, 1),
        "repeticoes": repeticoes,
        "voz":       voz,
    }


def _broadcast_thread(freqs_hz: list[int], ganho: int, iq_path: str):
    """Transmite IQ em cada frequência sequencialmente."""
    _emergencia["_parar"].clear()
    _emergencia["ativo"]      = True
    _emergencia["freq_total"] = len(freqs_hz)

    # Pausa sensores — emergência tem prioridade máxima
    hackrf_resource.zerar()
    sensor_hackrf.pausar()
    sensor_espectro.pausar()
    sensor_intel.pausar()

    for idx, freq_hz in enumerate(freqs_hz):
        if _emergencia["_parar"].is_set():
            break

        freq_mhz = freq_hz / 1e6
        _emergencia["freq_idx"]   = idx + 1
        _emergencia["freq_atual"] = freq_mhz
        _emergencia["progresso"]  = f"Transmitindo {freq_mhz} MHz ({idx+1}/{len(freqs_hz)})"
        print(f"  🚨  EMERGÊNCIA: {freq_mhz} MHz (ganho {ganho} dB)")

        if not hackrf_resource.acquire("emergencia", timeout=10.0):
            hackrf_resource.zerar()
            hackrf_resource.acquire("emergencia", timeout=5.0)

        try:
            proc = subprocess.Popen(
                ["hackrf_transfer",
                 "-t", iq_path,
                 "-f", str(freq_hz),
                 "-s", "2000000",
                 "-x", str(ganho),
                 "-a", "1"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # Aguarda transmissão terminar (arquivo acaba) ou sinal de parar
            while proc.poll() is None:
                if _emergencia["_parar"].is_set():
                    proc.terminate()
                    break
                time.sleep(0.2)
        except Exception as e:
            print(f"  🚨  Erro broadcast: {e}")
        finally:
            hackrf_resource.release()

        # Pausa de 1s entre frequências
        if not _emergencia["_parar"].is_set():
            time.sleep(1.0)

    _emergencia["ativo"]     = False
    _emergencia["progresso"] = "Broadcast concluído"
    _emergencia["freq_atual"] = None
    sensor_hackrf.retomar()
    sensor_espectro.retomar()
    sensor_intel.retomar()
    print("  🚨  EMERGÊNCIA: broadcast concluído")


@app.post("/api/emergencia/transmitir")
async def emergencia_transmitir(body: dict):
    """
    Inicia broadcast de emergência multi-frequência.
    Body: { "freqs_mhz": [93.7, 98.1, 100.7], "ganho": 47 }
    O IQ deve ter sido preparado via /api/emergencia/preparar antes.
    """
    if _emergencia["ativo"]:
        raise HTTPException(409, "Broadcast já em andamento")

    iq_path = _emergencia.get("iq_arquivo")
    if not iq_path or not os.path.exists(iq_path):
        raise HTTPException(400, "Prepare o IQ primeiro via /api/emergencia/preparar")

    freqs_mhz = body.get("freqs_mhz", [])
    if not freqs_mhz:
        raise HTTPException(400, "Liste ao menos uma frequência")

    ganho     = max(20, min(47, int(body.get("ganho", 47))))
    freqs_hz  = [int(f * 1e6) for f in freqs_mhz]

    t = threading.Thread(
        target=_broadcast_thread,
        args=(freqs_hz, ganho, iq_path),
        daemon=True, name="emergencia-broadcast"
    )
    t.start()
    _emergencia["_thread"] = t

    return {"ok": True, "freqs": freqs_mhz, "ganho": ganho}


@app.post("/api/emergencia/parar")
async def emergencia_parar():
    """Para o broadcast de emergência em andamento."""
    _emergencia["_parar"].set()
    subprocess.run(["pkill", "-9", "-f", "hackrf_transfer"], capture_output=True)
    _emergencia["ativo"]     = False
    _emergencia["progresso"] = "Broadcast interrompido"
    sensor_hackrf.retomar(); sensor_espectro.retomar(); sensor_intel.retomar()
    return {"ok": True}


@app.get("/api/llm/status")
async def llm_status():
    """Estado do LLM local e modelos disponíveis."""
    if not _LLM_OK:
        return {"ok": False, "erro": "llm_client não disponível"}
    try:
        modelos = llm_client.detectar_modelos()
        return {
            "ok":            True,
            "ollama":        True,
            "embed_model":   modelos.get("embed"),
            "chat_model":    modelos.get("chat"),
            "todos_modelos": modelos.get("todos", []),
            "chat_pronto":   modelos.get("chat") is not None,
            "instrucao":     None if modelos.get("chat") else
                "ollama pull qwen2.5:3b   ← 1.9 GB, ótimo em português",
        }
    except Exception as e:
        return {"ok": False, "erro": str(e)}


@app.post("/api/llm/chat")
async def llm_chat(body: dict):
    """
    Chat com o LLM local com contexto RF em tempo real.
    Body: { "mensagem": "...", "historico": [...] }
    """
    if not _LLM_OK:
        raise HTTPException(503, "LLM não disponível")

    mensagem = body.get("mensagem", "").strip()
    if not mensagem:
        raise HTTPException(400, "mensagem vazia")

    historico = body.get("historico", [])

    # Monta contexto RF atual
    try:
        ctx = {
            "rssi":      None,
            "variancia": None,
            "espectro":  sensor_espectro.estado(),
            "sinais":    sensor_intel.inteligencia().get("sinais", []),
            "hackrf":    sensor_hackrf.estado(),
        }
    except Exception:
        ctx = {}

    loop = asyncio.get_event_loop()
    try:
        resposta = await loop.run_in_executor(
            None, llm_client.chat, mensagem, ctx, None, historico
        )
    except Exception as e:
        raise HTTPException(500, f"Erro LLM: {e}")

    return {"ok": True, "resposta": resposta}


@app.post("/api/llm/classificar")
async def llm_classificar(body: dict):
    """
    Classifica um sinal por embedding (nomic-embed-text).
    Body: { "freq_mhz": 433.92, "dbm": -68, "descricao": "..." }
    """
    if not _LLM_OK:
        raise HTTPException(503, "LLM não disponível")

    freq  = body.get("freq_mhz", 0)
    dbm   = body.get("dbm", -80)
    desc  = body.get("descricao", "")

    loop = asyncio.get_event_loop()
    resultado = await loop.run_in_executor(
        None, llm_client.classificar_sinal, freq, dbm, desc
    )
    return {"ok": True, **resultado}


# WebSocket streaming de chat (para respostas longas)
@app.websocket("/ws/llm")
async def ws_llm(ws: WebSocket):
    """WebSocket para chat com streaming de tokens do LLM."""
    await ws.accept()
    if not _LLM_OK:
        await ws.send_text(json.dumps({"erro": "LLM não disponível"}))
        await ws.close()
        return
    try:
        while True:
            raw = await ws.receive_text()
            body = json.loads(raw)
            mensagem = body.get("mensagem", "").strip()
            if not mensagem:
                continue

            historico = body.get("historico", [])
            ctx = {
                "espectro": sensor_espectro.estado(),
                "sinais":   sensor_intel.inteligencia().get("sinais", []),
                "hackrf":   sensor_hackrf.estado(),
            }

            await ws.send_text(json.dumps({"tipo": "inicio"}))
            loop = asyncio.get_event_loop()

            # Streaming de tokens via executor (llm_client usa generator síncrono)
            def _stream():
                chunks = []
                for chunk in llm_client.chat_stream(mensagem, ctx, None, historico):
                    chunks.append(chunk)
                return chunks

            chunks = await loop.run_in_executor(None, _stream)
            full = "".join(chunks)
            await ws.send_text(json.dumps({"tipo": "texto", "conteudo": full}))
            await ws.send_text(json.dumps({"tipo": "fim"}))

    except (WebSocketDisconnect, Exception):
        pass


# ─── Emergência: contatos e SMS ───────────────────────────────────────────────

_CONTATOS_PATH = Path(__file__).parent / "contatos_emergencia.json"

_sms_status: dict = {"enviados": 0, "falhas": 0, "pendentes": 0, "ativo": False, "log": []}


def _carregar_contatos() -> list:
    if _CONTATOS_PATH.exists():
        try:
            return json.loads(_CONTATOS_PATH.read_text())
        except Exception:
            return []
    return []


def _salvar_contatos(lista: list):
    _CONTATOS_PATH.write_text(json.dumps(lista, ensure_ascii=False, indent=2))


@app.get("/api/emergencia/contatos")
async def contatos_listar():
    return {"contatos": _carregar_contatos()}


@app.post("/api/emergencia/contatos")
async def contatos_salvar(body: dict):
    lista = body.get("contatos", [])
    _salvar_contatos(lista)
    return {"ok": True, "total": len(lista)}


async def _enviar_sms_twilio(para: str, mensagem: str) -> dict:
    import urllib.request, urllib.parse, base64
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    auth_token  = os.environ.get("TWILIO_AUTH_TOKEN", "")
    from_number = os.environ.get("TWILIO_FROM_NUMBER", "")
    if not all([account_sid, auth_token, from_number]):
        return {"ok": False, "erro": "Twilio não configurado — adicione TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN e TWILIO_FROM_NUMBER ao .env"}
    url  = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    data = urllib.parse.urlencode({"To": para, "From": from_number, "Body": mensagem}).encode()
    cred = base64.b64encode(f"{account_sid}:{auth_token}".encode()).decode()
    req  = urllib.request.Request(url, data=data, headers={
        "Authorization": f"Basic {cred}",
        "Content-Type":  "application/x-www-form-urlencoded",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            r = json.loads(resp.read())
            return {"ok": True, "sid": r.get("sid", "")}
    except Exception as e:
        return {"ok": False, "erro": str(e)}


async def _disparar_sms_async(mensagem: str, contatos: list):
    _sms_status.update({"enviados": 0, "falhas": 0, "pendentes": len(contatos), "ativo": True, "log": []})
    for c in contatos:
        tel = c.get("telefone", "")
        if not tel:
            _sms_status["pendentes"] = max(0, _sms_status["pendentes"] - 1)
            continue
        r = await _enviar_sms_twilio(tel, mensagem)
        entry = {"nome": c.get("nome", tel), "telefone": tel, **r}
        _sms_status["log"].append(entry)
        if r["ok"]:
            _sms_status["enviados"] += 1
        else:
            _sms_status["falhas"] += 1
        _sms_status["pendentes"] = max(0, _sms_status["pendentes"] - 1)
    _sms_status["ativo"] = False


@app.post("/api/emergencia/sms")
async def emergencia_sms(body: dict):
    """Dispara SMS para contatos. Body: { mensagem, contatos? }"""
    mensagem = body.get("mensagem", "").strip()
    if not mensagem:
        raise HTTPException(400, "mensagem vazia")
    contatos = body.get("contatos") or _carregar_contatos()
    if not contatos:
        raise HTTPException(400, "nenhum contato cadastrado")
    asyncio.create_task(_disparar_sms_async(mensagem, contatos))
    return {"ok": True, "total": len(contatos)}


@app.get("/api/emergencia/sms/status")
async def emergencia_sms_status():
    return _sms_status


@app.post("/api/emergencia/disparar")
async def emergencia_disparar(body: dict):
    """
    Dispara FM broadcast + SMS simultaneamente.
    Body: { "texto": "...", "voz": "Luciana", "repeticoes": 2,
            "freqs_mhz": [...], "ganho": 47, "contatos": [...] }
    """
    if _emergencia["ativo"]:
        raise HTTPException(409, "Broadcast FM já em andamento — pare antes de disparar novamente")

    texto = body.get("texto", "").strip()
    if not texto:
        raise HTTPException(400, "texto vazio")

    voz       = body.get("voz", "Luciana")
    repeticoes = max(1, min(6, int(body.get("repeticoes", 2))))
    ganho     = max(20, min(47, int(body.get("ganho", 47))))
    freqs_mhz = body.get("freqs_mhz") or [round(88.0 + i * 0.2, 1) for i in range(101)]

    loop = asyncio.get_event_loop()

    # 1. TTS → WAV
    try:
        import io as _io, wave as _wave
        wav_bytes = await loop.run_in_executor(None, _tts_para_wav, texto, voz)
        with _wave.open(_io.BytesIO(wav_bytes), "rb") as wf:
            sr = wf.getframerate()
            raw = wf.readframes(wf.getnframes())
        silencio = b"\x00\x00" * sr
        pcm_total = (raw + silencio) * repeticoes
        buf = _io.BytesIO()
        with _wave.open(buf, "wb") as wf2:
            wf2.setnchannels(1); wf2.setsampwidth(2); wf2.setframerate(sr)
            wf2.writeframes(pcm_total)
        wav_final = buf.getvalue()
    except Exception as e:
        raise HTTPException(500, f"TTS falhou: {e}")

    # 2. WAV → IQ FM
    try:
        iq_bytes = await loop.run_in_executor(None, _modular_fm_iq, wav_final)
    except Exception as e:
        raise HTTPException(500, f"Modulação FM falhou: {e}")

    sinais_path = Path(__file__).parent / "sinais"
    sinais_path.mkdir(exist_ok=True)
    iq_path = str(sinais_path / "_emergencia.iq")
    with open(iq_path, "wb") as f:
        f.write(iq_bytes)
    _emergencia["iq_arquivo"] = iq_path

    # 3. FM broadcast em thread
    freqs_hz = [int(f * 1e6) for f in freqs_mhz]
    _emergencia["_parar"].clear()
    t = threading.Thread(target=_broadcast_thread, args=(freqs_hz, ganho, iq_path),
                         daemon=True, name="emergencia-broadcast")
    t.start()
    _emergencia["_thread"] = t

    # 4. SMS em paralelo (não bloqueia o FM)
    contatos = body.get("contatos") or _carregar_contatos()
    if contatos:
        asyncio.create_task(_disparar_sms_async(texto, contatos))

    duracao_s = len(iq_bytes) / (2 * 2_000_000)
    return {
        "ok": True,
        "fm_freqs":      len(freqs_mhz),
        "sms_contatos":  len(contatos),
        "duracao_audio": round(duracao_s, 1),
        "repeticoes":    repeticoes,
    }


app.mount("/", StaticFiles(directory=str(UI_PATH), html=True), name="ui")

if __name__ == "__main__":
    print()
    print("  📡  mtzHRF — Plataforma RF + Áudio + HackRF")
    print(f"  🌐  http://localhost:{PORTA}")
    print(f"  ℹ️   HackRF: scan 2.4GHz + doppler + espectro 88-900MHz")
    print()
    uvicorn.run(app, host="0.0.0.0", port=PORTA, log_level="warning")
