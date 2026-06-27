"""
mtzRF — Sensor HackRF One
Dois modos:
  1. Scanner de canais WiFi 2.4 GHz (potência por canal)
  2. Detector Doppler: captura variações de magnitude a 2.437 GHz
     → extrai frequência respiratória e detecta presença

Quando HackRF não está conectado, funciona em modo stub silencioso.
"""

import subprocess
import threading
import time
import numpy as np
from collections import deque
import hackrf_resource

# ─── Configuração — Scanner de Canais ─────────────────────────────────────────
CANAIS_2G = {
    1:  2_412_000_000,
    6:  2_437_000_000,
    11: 2_462_000_000,
}
TAXA_AMOSTRA   = 2_000_000   # 2 Msps
N_AMOSTRAS     = 200_000     # 100ms de dados IQ por canal
GANHO_LNA      = 40
GANHO_VGA      = 24
INTERVALO_SCAN = 5.0         # segundos entre varreduras

# ─── Configuração — Doppler Corporal ──────────────────────────────────────────
FREQ_DOPPLER   = 2_437_000_000   # 2.437 GHz (WiFi CH6)
N_AMOSTRAS_DOP = 200_000          # 100ms por medição
INTERVALO_DOP  = 0.12             # ~8 medições/s → 8 Hz de série temporal
BUF_DOP_S      = 30               # 30s de histórico
BUF_DOP_N      = int(BUF_DOP_S / INTERVALO_DOP)
TAXA_DOPHz     = 1.0 / INTERVALO_DOP


class SensorHackRF:
    """
    Escaneia canais WiFi 2.4 GHz com HackRF e detecta presença/respiração
    via micro-Doppler na mesma frequência.
    """

    def __init__(self):
        self.disponivel    = False
        self.conectado     = False
        self.canais: dict  = {}
        self._hist: dict   = {c: deque(maxlen=30) for c in CANAIS_2G}

        # Doppler / detecção corporal
        self._buf_mag     = deque(maxlen=BUF_DOP_N)
        self.doppler_resp = 0.0
        self.doppler_conf = 0.0
        self.doppler_var  = 0.0
        self.doppler_pres = False

        self._lock         = threading.Lock()
        self._parar        = threading.Event()
        self._pausado      = threading.Event()   # set = pausado, clear = ativo
        self._thread_scan: threading.Thread | None = None
        self._thread_dop:  threading.Thread | None = None

        self._verificar()

    # ── Inicialização ──────────────────────────────────────────────────────────
    def _verificar(self):
        try:
            r = subprocess.run(
                ["hackrf_info"], capture_output=True, text=True, timeout=3
            )
            self.disponivel = True
            self.conectado  = "Serial number" in r.stdout or "Found HackRF" in r.stdout
            if self.conectado:
                print("  📻  HackRF One detectado — scanner + doppler prontos")
            else:
                print("  📻  HackRF tools OK — aguardando dispositivo")
        except FileNotFoundError:
            print("  ⚠   hackrf_info não encontrado (brew install hackrf)")
        except Exception as e:
            print(f"  ⚠   HackRF verificação: {e}")

    def iniciar(self):
        if not self.disponivel:
            return
        self._parar.clear()
        self._thread_scan = threading.Thread(
            target=self._loop_scan, daemon=True, name="hackrf-scan"
        )
        self._thread_dop = threading.Thread(
            target=self._loop_doppler, daemon=True, name="hackrf-doppler"
        )
        self._thread_scan.start()
        self._thread_dop.start()

    def parar(self):
        self._parar.set()

    def pausar(self):
        """Libera o HackRF para outro processo (ex: grgsm). Aguarda até 1s pela transferência em curso."""
        self._pausado.set()
        time.sleep(0.8)   # dá tempo para o hackrf_transfer em curso terminar

    def retomar(self):
        """Retoma captura após o HackRF ser devolvido."""
        self._pausado.clear()

    # ── Hot-plug ───────────────────────────────────────────────────────────────
    def _detectar_hotplug(self) -> bool:
        try:
            r = subprocess.run(
                ["hackrf_info"], capture_output=True, text=True, timeout=3
            )
            encontrado = "Serial number" in r.stdout or "Found HackRF" in r.stdout
            if encontrado and not self.conectado:
                print("  📻  HackRF conectado! Iniciando scan + doppler…")
                self.conectado = True
            elif not encontrado and self.conectado:
                print("  📻  HackRF desconectado.")
                self.conectado = False
                with self._lock:
                    self.canais.clear()
                    self._buf_mag.clear()
                    self.doppler_resp = 0.0
                    self.doppler_conf = 0.0
                    self.doppler_var  = 0.0
                    self.doppler_pres = False
            return encontrado
        except Exception:
            return False

    # ── Captura IQ → potência dBm ─────────────────────────────────────────────
    def _capturar_dbm(self, freq: int) -> float | None:
        if not hackrf_resource.acquire('scan', timeout=3.0):
            return None   # HackRF ocupado — pula este canal
        try:
            proc = subprocess.run(
                ["hackrf_transfer", "-r", "-",
                 "-f", str(freq),
                 "-s", str(TAXA_AMOSTRA),
                 "-n", str(N_AMOSTRAS),
                 "-g", str(GANHO_LNA),
                 "-l", str(GANHO_VGA),
                 "-a", "1"],
                capture_output=True, timeout=6
            )
            if proc.returncode != 0 or len(proc.stdout) < 200:
                return None
            raw = np.frombuffer(proc.stdout, dtype=np.int8).astype(np.float32)
            I, Q = raw[0::2], raw[1::2]
            pot  = float(np.mean(np.abs(I + 1j * Q) ** 2))
            return round(10 * np.log10(pot + 1e-9) - 80.0, 1)
        except subprocess.TimeoutExpired:
            return None
        except Exception:
            return None
        finally:
            hackrf_resource.release()

    # ── Captura IQ → magnitude instantânea (Doppler) ─────────────────────────
    def _capturar_mag(self, freq: int) -> float | None:
        """Retorna RMS de magnitude do bloco IQ — índice de potência instantânea."""
        if not hackrf_resource.acquire('doppler', timeout=1.0):
            return None   # HackRF ocupado — pula esta medição
        try:
            proc = subprocess.run(
                ["hackrf_transfer", "-r", "-",
                 "-f", str(freq),
                 "-s", str(TAXA_AMOSTRA),
                 "-n", str(N_AMOSTRAS_DOP),
                 "-g", str(GANHO_LNA),
                 "-l", str(GANHO_VGA),
                 "-a", "1"],
                capture_output=True, timeout=5
            )
            if proc.returncode != 0 or len(proc.stdout) < 100:
                return None
            raw = np.frombuffer(proc.stdout, dtype=np.int8).astype(np.float32)
            I, Q = raw[0::2], raw[1::2]
            return float(np.sqrt(np.mean(np.abs(I + 1j * Q) ** 2)))
        except Exception:
            return None
        finally:
            hackrf_resource.release()

    # ── FFT Doppler → BPM respiração ──────────────────────────────────────────
    def _analisar_doppler(self):
        n = len(self._buf_mag)
        if n < 40:
            return
        arr = np.array(list(self._buf_mag), dtype=float)
        arr -= arr.mean()
        var  = float(np.var(arr))
        pres = var > 1.5

        arr_w = arr * np.hanning(n)
        fft   = np.abs(np.fft.rfft(arr_w))
        freqs = np.fft.rfftfreq(n, d=1.0 / TAXA_DOPHz)
        mask  = (freqs >= 0.10) & (freqs <= 0.50)

        bpm, conf = 0.0, 0.0
        if pres and mask.any():
            idx_pico  = int(np.argmax(fft[mask]))
            freq_pico = float(freqs[mask][idx_pico])
            snr       = float(fft[mask][idx_pico]) / (float(fft.mean()) + 1e-9)
            if snr > 2.5 and 8 <= freq_pico * 60 <= 30:
                bpm  = round(freq_pico * 60, 1)
                conf = round(min(0.95, (snr - 2.5) / 8.0), 2)

        with self._lock:
            self.doppler_var  = round(var, 3)
            self.doppler_pres = pres
            self.doppler_resp = bpm
            self.doppler_conf = conf

    # ── Loop Scanner de Canais ─────────────────────────────────────────────────
    def _loop_scan(self):
        while not self._parar.is_set():
            if self._pausado.is_set():
                time.sleep(0.5)
                continue
            if not self._detectar_hotplug():
                time.sleep(3.0)
                continue
            for canal in CANAIS_2G:
                if self._parar.is_set() or self._pausado.is_set():
                    break
                pot = self._capturar_dbm(CANAIS_2G[canal])
                if pot is not None:
                    with self._lock:
                        self._hist[canal].append(pot)
                        hist = list(self._hist[canal])
                        var  = float(np.var(hist)) if len(hist) > 2 else 0.0
                        self.canais[canal] = {
                            "potencia_dbm": pot,
                            "variancia":    round(var, 3),
                            "historico":    [round(v, 1) for v in hist[-20:]],
                        }
            time.sleep(INTERVALO_SCAN)

    # ── Loop Doppler ──────────────────────────────────────────────────────────
    def _loop_doppler(self):
        while not self._parar.is_set():
            if self._pausado.is_set() or not self.conectado:
                time.sleep(0.5)
                continue
            mag = self._capturar_mag(FREQ_DOPPLER)
            if mag is not None:
                with self._lock:
                    self._buf_mag.append(mag)
                if len(self._buf_mag) % 8 == 0:
                    self._analisar_doppler()
            time.sleep(INTERVALO_DOP)

    # ── Estado ────────────────────────────────────────────────────────────────
    def estado(self) -> dict:
        with self._lock:
            return {
                "disponivel": self.disponivel,
                "conectado":  self.conectado,
                "canais":     dict(self.canais),
                "doppler": {
                    "presente":   self.doppler_pres,
                    "resp_bpm":   self.doppler_resp,
                    "confianca":  self.doppler_conf,
                    "variancia":  self.doppler_var,
                    "n_amostras": len(self._buf_mag),
                },
            }


# ─── Teste ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n📻 mtzRF — Teste HackRF (scan + doppler)\n")
    s = SensorHackRF()
    if s.conectado:
        s.iniciar()
        print("Escaneando… (Ctrl+C para parar)\n")
        try:
            while True:
                time.sleep(2)
                e = s.estado()
                dop = e["doppler"]
                print(f"  Doppler: pres={dop['presente']}  "
                      f"resp={dop['resp_bpm']} bpm  conf={dop['confianca']:.2f}  "
                      f"var={dop['variancia']:.2f}  n={dop['n_amostras']}")
                for canal, info in e["canais"].items():
                    print(f"    CH{canal:2d}  {info['potencia_dbm']:6.1f} dBm  "
                          f"var={info['variancia']:.3f}")
        except KeyboardInterrupt:
            s.parar()
    else:
        print("HackRF não conectado.")
