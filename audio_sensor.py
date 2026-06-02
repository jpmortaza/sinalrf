"""
SinalRF — Sensor de Áudio
Detecta respiração e batimentos cardíacos via microfone.
Funciona com microfone do Mac ou AirPods Pro (automático).
"""

import threading
import time
import math
from collections import deque

import numpy as np
import sounddevice as sd
from scipy import signal as sp_signal

# ─── Configuração ──────────────────────────────────────────────────────────────
TAXA          = 44100    # Hz
CHUNK         = 2048     # ~46ms por bloco
TAXA_ENV      = 10       # Hz — taxa do envelope RMS (para FFT de respiração)
BUF_ENV_S     = 40       # segundos de histórico do envelope
BUF_ENV_N     = BUF_ENV_S * TAXA_ENV

# Nomes parciais reconhecidos como AirPods
NOMES_AIRPODS = ["airpod", "airpods"]


# ─── Utilitários ───────────────────────────────────────────────────────────────
def _encontrar_dispositivo(nomes_parciais: list[str]) -> int | None:
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            if any(p in d["name"].lower() for p in nomes_parciais):
                return i
    return None


def _listar_dispositivos_entrada() -> list[dict]:
    return [
        {"idx": i, "nome": d["name"], "canais": d["max_input_channels"]}
        for i, d in enumerate(sd.query_devices())
        if d["max_input_channels"] > 0
    ]


# ─── SensorRespiracao ──────────────────────────────────────────────────────────
class SensorRespiracao:
    """
    Detecta frequência respiratória analisando o envelope RMS do áudio.
    A respiração modula o ruído ambiente a 0.1–0.55 Hz (6–33 rpm).
    """

    def __init__(self):
        self.buf_env = deque(maxlen=BUF_ENV_N)
        self._chunks_acc: list[np.ndarray] = []
        self._chunks_por_tick = max(1, TAXA // (TAXA_ENV * CHUNK))
        self.bpm = 0.0
        self.confianca = 0.0

    def alimentar(self, bloco: np.ndarray):
        self._chunks_acc.append(bloco)
        if len(self._chunks_acc) >= self._chunks_por_tick:
            concat = np.concatenate(self._chunks_acc)
            rms = float(np.sqrt(np.mean(concat ** 2) + 1e-12))
            self.buf_env.append(rms)
            self._chunks_acc.clear()
            self._analisar()

    def _analisar(self):
        n = len(self.buf_env)
        if n < TAXA_ENV * 8:
            return
        arr = np.array(list(self.buf_env), dtype=float)
        arr -= arr.mean()
        arr *= np.hanning(n)
        fft   = np.abs(np.fft.rfft(arr))
        freqs = np.fft.rfftfreq(n, d=1.0 / TAXA_ENV)
        mask  = (freqs >= 0.10) & (freqs <= 0.55)
        if not mask.any():
            return
        fft_banda = fft[mask]
        idx_pico  = int(np.argmax(fft_banda))
        freq_pico = float(freqs[mask][idx_pico])
        snr = float(fft_banda[idx_pico]) / (float(fft.mean()) + 1e-9)
        if snr > 2.0 and freq_pico > 0:
            self.bpm       = round(freq_pico * 60, 1)
            self.confianca = round(min(1.0, (snr - 2.0) / 8.0), 2)
        elif snr < 1.5:
            self.confianca = max(0.0, self.confianca - 0.05)

    def estado(self) -> dict:
        return {"bpm": self.bpm, "confianca": self.confianca,
                "fonte": "audio", "n_amostras": len(self.buf_env)}


# ─── SensorBatimentos ──────────────────────────────────────────────────────────
class SensorBatimentos:
    """
    Estima batimentos cardíacos analisando baixas frequências no áudio in-ear.
    Requer AirPods Pro para maior precisão.
    """

    def __init__(self):
        self._buf_dec = deque(maxlen=200 * 15)
        self._acc_dec: list[float] = []
        self._fator_dec = TAXA // 200
        self.bpm = 0.0
        self.confianca = 0.0
        self._n_ali = 0
        self._is_airpods = False

    def alimentar(self, bloco: np.ndarray):
        dec = bloco[:: self._fator_dec]
        self._buf_dec.extend(dec.tolist())
        self._n_ali += 1
        if self._n_ali % (TAXA_ENV * 2) == 0:
            self._analisar()

    def _analisar(self):
        n = len(self._buf_dec)
        if n < 200 * 5:
            return
        arr = np.array(list(self._buf_dec), dtype=float)
        arr -= arr.mean()
        env = np.abs(arr)
        b, a = sp_signal.butter(2, 4.0 / (200 / 2), btype="low")
        env_f = sp_signal.filtfilt(b, a, env)
        env_f -= env_f.mean()
        fft   = np.abs(np.fft.rfft(env_f * np.hanning(n)))
        freqs = np.fft.rfftfreq(n, d=1.0 / 200)
        mask  = (freqs >= 0.7) & (freqs <= 2.5)
        if not mask.any():
            return
        fft_banda = fft[mask]
        idx_pico  = int(np.argmax(fft_banda))
        freq_pico = float(freqs[mask][idx_pico])
        snr       = float(fft_banda[idx_pico]) / (float(fft.mean()) + 1e-9)
        if snr > 3.0 and freq_pico > 0:
            self.bpm = round(freq_pico * 60, 0)
            conf_max = 0.85 if self._is_airpods else 0.35
            self.confianca = round(min(conf_max, (snr - 3.0) / 12.0), 2)
        else:
            self.confianca = max(0.0, self.confianca - 0.03)

    def estado(self) -> dict:
        return {"bpm": self.bpm, "confianca": self.confianca, "fonte": "audio_inear"}


# ─── SensorAudio (orquestrador) ────────────────────────────────────────────────
class SensorAudio:
    """
    Gerencia microfone, detecta respiração e batimentos.
    Troca automaticamente para AirPods Pro quando conectado.
    """

    def __init__(self):
        self.resp    = SensorRespiracao()
        self.card    = SensorBatimentos()

        self._stream: sd.InputStream | None = None
        self._thread: threading.Thread | None = None
        self._parar  = threading.Event()

        self.dispositivo_nome = "—"
        self.dispositivo_idx  = None
        self.is_airpods       = False
        self.ativo            = False
        self.amplitude_db     = -80.0
        self._buf_onda        = deque(maxlen=512)

    def iniciar(self):
        self._parar.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="audio-sensor")
        self._thread.start()

    def parar(self):
        self._parar.set()
        if self._stream:
            self._stream.stop()

    def _loop(self):
        while not self._parar.is_set():
            idx_ap = _encontrar_dispositivo(NOMES_AIRPODS)
            idx    = idx_ap if idx_ap is not None else None
            nome   = sd.query_devices(idx)["name"] if idx is not None \
                     else sd.query_devices(kind="input")["name"]

            self.dispositivo_idx  = idx
            self.dispositivo_nome = nome
            self.is_airpods       = idx_ap is not None
            self.card._is_airpods = self.is_airpods
            self.ativo            = True

            icone = "🎧" if self.is_airpods else "🎙"
            print(f"  {icone}  Áudio: {nome}")

            try:
                self._gravar(idx)
            except Exception as e:
                print(f"  [SensorAudio] erro: {e}")
                time.sleep(2)

    def _gravar(self, idx: int | None):
        kw = {"samplerate": TAXA, "channels": 1, "dtype": "float32", "blocksize": CHUNK}
        if idx is not None:
            kw["device"] = idx

        def callback(indata, frames, time_info, status):
            mono = indata[:, 0].copy()
            rms  = float(np.sqrt(np.mean(mono ** 2) + 1e-12))
            self.amplitude_db = 20 * math.log10(rms + 1e-6)
            step = max(1, len(mono) // 32)
            self._buf_onda.extend(mono[::step].tolist())
            self.resp.alimentar(mono)
            self.card.alimentar(mono)

        with sd.InputStream(**kw, callback=callback):
            while not self._parar.is_set():
                novo_idx = _encontrar_dispositivo(NOMES_AIRPODS)
                if novo_idx != self.dispositivo_idx:
                    print("  🔄  Dispositivo de áudio mudou — reconectando…")
                    break
                time.sleep(1.0)

    def estado(self) -> dict:
        onda = list(self._buf_onda)[-128:]
        return {
            "ativo":        self.ativo,
            "is_airpods":   self.is_airpods,
            "dispositivo":  self.dispositivo_nome,
            "amplitude_db": round(self.amplitude_db, 1),
            "onda":         [round(v, 4) for v in onda],
            "respiracao":   self.resp.estado(),
            "batimentos":   self.card.estado(),
        }

    def listar_dispositivos(self) -> list[dict]:
        return _listar_dispositivos_entrada()


# ─── Teste independente ────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n📡 SinalRF — Teste de Áudio\n")
    s = SensorAudio()
    s.iniciar()
    print("Capturando… (Ctrl+C para parar)\n")
    try:
        while True:
            time.sleep(2)
            e = s.estado()
            print(f"  🎙 {e['dispositivo']:30s}  dB={e['amplitude_db']:5.1f}  "
                  f"resp={e['respiracao']['bpm']:4.1f}rpm  card={e['batimentos']['bpm']:4.0f}bpm")
    except KeyboardInterrupt:
        s.parar()
