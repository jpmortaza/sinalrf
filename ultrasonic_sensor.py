"""
mtzRF — Monitor Ultrassônico (TSCM)

Captura áudio na maior taxa suportada pela placa (tenta 192k → 96k → 48k) e
vigia a banda acima da voz humana para achar ameaças que o ouvido não pega:

  • Beacons de rastreamento near-ultrassônico (18–20 kHz): cross-device tracking,
    "data-over-sound" — um aparelho emite um tom inaudível que outro escuta.
  • Exfiltração de dados por ultrassom (escuta que vaza áudio fora da faixa audível).
  • Jammers de áudio ultrassônicos (energia larga acima de 20 kHz).

Independente do HackRF (usa o microfone do PC). Pode rodar em paralelo ao
audio_sensor — no Windows o WASAPI compartilha o dispositivo de entrada.

HONESTIDADE: o que aparece acima de ~20 kHz depende da CÁPSULA do microfone.
Mics comuns rolam off em 16–20 kHz; aí a faixa útil é a near-ultrassônica
(18–20 kHz), que já cobre os beacons de rastreamento. Para ultrassom pleno
(>24 kHz) use um mic ultrassônico (ex.: Dodotronic UltraMic 192/250k).
"""

import threading
import time
import queue
from collections import deque

import numpy as np

try:
    import sounddevice as sd
    _SD_OK = True
    _SD_ERRO = None
except Exception as e:  # pragma: no cover
    _SD_OK = False
    _SD_ERRO = f"{type(e).__name__}: {e}"

TAXAS_PREF   = [192000, 96000, 48000]
NFFT         = 8192          # @192k: ~23 Hz/bin, ~43 ms/quadro
BINS_UI      = 240           # bins enviados p/ a UI (max-pooling)
F_MIN_TOM    = 17000.0       # só caça tons acima disso (acima da voz/música)
MARGEM_DB    = 12.0          # quão acima do piso um pico precisa estar
BUCKET_HZ    = 200.0         # agrupa picos vizinhos no mesmo "tom"
IDADE_TOM_S  = 5.0           # remove tom não visto há mais de X s
JAMMER_FRAC  = 0.30          # fração de bins >20k elevados => possível jammer


def _classe(freq: float) -> str:
    if freq < 20000:
        return "near-ultrassônico (beacon/rastreamento?)"
    if freq < 24000:
        return "ultrassônico (borda audível)"
    return "ultrassônico"


class MonitorUltrassom:
    def __init__(self, device=None):
        self.disponivel = _SD_OK
        self.erro       = _SD_ERRO
        self.device     = device
        self.device_nome = None
        self.sr         = None
        self.nyquist    = None

        self._parar   = False
        self._rodando = False
        self._thread  = None
        self._lock    = threading.Lock()

        self._freqs_ui = None     # Hz por bin de UI
        self._espectro = None     # dB por bin de UI (atual)
        self._piso     = -120.0
        self._tons: dict[float, dict] = {}
        self._jammer   = False
        self._ts       = 0.0

    # ── seleção de taxa: abrir de verdade é o teste real ────────────────────
    def _escolher_sr(self) -> int | None:
        for sr in TAXAS_PREF:
            try:
                with sd.InputStream(device=self.device, samplerate=sr,
                                    channels=1, dtype="float32", blocksize=1024):
                    return sr
            except Exception:
                continue
        return None

    # ── loop de captura + análise ───────────────────────────────────────────
    def _loop(self):
        sr = self._escolher_sr()
        if sr is None:
            self.erro = "nenhuma taxa de entrada abriu"
            self._rodando = False
            return
        self.sr = sr
        self.nyquist = sr // 2
        try:
            self.device_nome = sd.query_devices(
                self.device if self.device is not None else sd.default.device[0],
                "input")["name"]
        except Exception:
            self.device_nome = "entrada padrão"

        win   = np.hanning(NFFT)
        freqs = np.fft.rfftfreq(NFFT, 1.0 / sr)
        # mapeia bins FFT -> bins de UI (max-pooling)
        idx_ui = np.linspace(0, len(freqs), BINS_UI + 1).astype(int)
        freqs_ui = np.array([freqs[a:b].mean() if b > a else freqs[min(a, len(freqs)-1)]
                             for a, b in zip(idx_ui[:-1], idx_ui[1:])])
        with self._lock:
            self._freqs_ui = freqs_ui

        q: "queue.Queue" = queue.Queue(maxsize=32)

        def cb(indata, frames, t, status):
            try:
                q.put_nowait(indata[:, 0].copy())
            except queue.Full:
                pass

        try:
            with sd.InputStream(device=self.device, samplerate=sr, channels=1,
                                dtype="float32", blocksize=NFFT, callback=cb):
                self._rodando = True
                self.erro = None
                buf = np.zeros(0, np.float32)
                while not self._parar:
                    try:
                        blk = q.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    buf = np.concatenate([buf, blk])
                    while len(buf) >= NFFT:
                        self._analisar(buf[:NFFT], win, freqs, idx_ui)
                        buf = buf[NFFT:]
        except Exception as e:
            self.erro = f"{type(e).__name__}: {e}"
        finally:
            self._rodando = False

    def _analisar(self, frame, win, freqs, idx_ui):
        X   = np.abs(np.fft.rfft(frame * win)) / NFFT
        psd = 20.0 * np.log10(X + 1e-9)
        piso = float(np.median(psd))

        # espectro p/ UI (max-pooling -> realça picos finos)
        esp_ui = np.array([psd[a:b].max() if b > a else psd[min(a, len(psd)-1)]
                           for a, b in zip(idx_ui[:-1], idx_ui[1:])])

        # detecção de tons acima de F_MIN_TOM
        reg = freqs >= F_MIN_TOM
        acima = np.where(reg & (psd > piso + MARGEM_DB))[0]
        agora = time.time()

        # agrupa índices contíguos em picos
        achados = []
        if acima.size:
            grupos = np.split(acima, np.where(np.diff(acima) > 1)[0] + 1)
            for g in grupos:
                ip = g[int(np.argmax(psd[g]))]
                achados.append((float(freqs[ip]), float(psd[ip] - piso)))

        # possível jammer: muitos bins >20k elevados
        reg20 = freqs >= 20000
        frac = float(np.mean(psd[reg20] > piso + 6.0)) if reg20.any() else 0.0
        jammer = frac >= JAMMER_FRAC

        with self._lock:
            self._espectro = np.round(esp_ui, 1)
            self._piso = round(piso, 1)
            self._jammer = jammer
            self._ts = agora
            # atualiza/insere tons por bucket de frequência
            for f, nivel in achados:
                k = round(f / BUCKET_HZ) * BUCKET_HZ
                t = self._tons.get(k)
                if t is None:
                    self._tons[k] = {"freq": f, "nivel": nivel, "nivel_max": nivel,
                                     "classe": _classe(f), "t0": agora, "tn": agora, "n": 1}
                else:
                    t["freq"] = f; t["nivel"] = nivel
                    t["nivel_max"] = max(t["nivel_max"], nivel)
                    t["tn"] = agora; t["n"] += 1
            # envelhece sumidos
            for k in [k for k, t in self._tons.items() if agora - t["tn"] > IDADE_TOM_S]:
                self._tons.pop(k, None)

    # ── controle ─────────────────────────────────────────────────────────────
    def iniciar(self) -> bool:
        if not self.disponivel:
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._parar = False
        self._thread = threading.Thread(target=self._loop, daemon=True, name="ultrassom")
        self._thread.start()
        return True

    def parar(self):
        self._parar = True

    # ── estado p/ UI ─────────────────────────────────────────────────────────
    def estado(self) -> dict:
        agora = time.time()
        with self._lock:
            tons = []
            for t in self._tons.values():
                tons.append({
                    "freq_hz": round(t["freq"], 1),
                    "freq_khz": round(t["freq"] / 1000, 2),
                    "nivel_db": round(t["nivel"], 1),
                    "nivel_max_db": round(t["nivel_max"], 1),
                    "classe": t["classe"],
                    "persistencia": t["n"],
                    "idade": round(agora - t["tn"], 1),
                })
            tons.sort(key=lambda x: -x["nivel_db"])
            return {
                "disponivel": self.disponivel,
                "rodando": self._rodando,
                "erro": self.erro,
                "sr": self.sr,
                "nyquist": self.nyquist,
                "device": self.device_nome,
                "piso_db": self._piso,
                "jammer": self._jammer,
                "freqs": [round(float(f), 1) for f in self._freqs_ui] if self._freqs_ui is not None else [],
                "espectro": [float(v) for v in self._espectro] if self._espectro is not None else [],
                "tons": tons,
                "ts": round(self._ts, 2),
            }


# ── teste manual ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    m = MonitorUltrassom()
    if not m.iniciar():
        print("indisponível:", m.erro); raise SystemExit(1)
    print("Capturando ultrassom… Ctrl+C p/ sair")
    try:
        while True:
            time.sleep(2)
            e = m.estado()
            print(f"\nsr={e['sr']} nyq={e['nyquist']} piso={e['piso_db']}dB "
                  f"jammer={e['jammer']} tons={len(e['tons'])}")
            for t in e["tons"][:6]:
                print(f"  {t['freq_khz']:>6} kHz  +{t['nivel_db']:.1f} dB  "
                      f"x{t['persistencia']}  {t['classe']}")
    except KeyboardInterrupt:
        m.parar()
