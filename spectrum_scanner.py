"""
SinalRF — Scanner de Espectro Wideband
Usa hackrf_sweep para varrer 88–1000 MHz.
Detecta sinais de rádio FM, anomalias (sinais fantasmas) e
monitora todo o espectro visível ao HackRF.
"""

import subprocess
import threading
import time
import numpy as np
from collections import deque
import hackrf_resource

# ─── Tabela de emissoras FM BR (frequências comuns) ───────────────────────────
_FM_NOMES = {
    89.1: "CBN",         89.5: "Jovem Pan",   90.1: "Antena 1",
    91.3: "Mix FM",      92.1: "Nova Brasil",  93.7: "Transamérica",
    94.7: "Globo FM",    95.1: "Metropolitana",96.5: "Rádio Bandeirantes",
    97.5: "Jovem Pan 2", 98.1: "Cultura FM",   99.3: "Eldorado",
   100.1: "Boa Vontade",101.7: "Gospel",      102.1: "Vanguarda",
   103.3: "Terra",      104.7: "Pop Rock",    105.1: "Rede Aleluia",
   106.3: "Rádio 9",    107.1: "Tropical",
}

# ─── Configuração ─────────────────────────────────────────────────────────────
BANDAS = {
    "fm":       (88,   108),
    "vhf_low":  (108,  300),
    "uhf":      (300,  512),
    "celular":  (700,  900),
}
BIN_FM      = 150_000   # 150 kHz res para FM
BIN_WIDE    = 1_000_000 # 1 MHz para o resto
INTERVALO   = 5.0       # segundos entre varreduras
N_HIST      = 40        # frames no histórico do waterfall
THR_ANOMALIA = 12.0     # dB acima do baseline = sinal fantasma


# ─── ScannerEspectro ──────────────────────────────────────────────────────────
class ScannerEspectro:
    """
    Varre o espectro em segundo plano e expõe:
    - espectro atual (freq_mhz, dbm)
    - histórico para waterfall
    - sinais FM detectados
    - sinais anômalos (fantasmas)
    """

    def __init__(self):
        self.disponivel   = False
        self.conectado    = False
        self.espectro:   list = []        # [(freq_mhz, dbm), ...]
        self.historico       = deque(maxlen=N_HIST)
        self._baseline:  dict = {}        # {freq_mhz: dbm_avg}
        self._baseline_n     = 0
        self.sinais_anomalos: list = []
        self.sinais_fm:       list = []
        self._lock    = threading.Lock()
        self._parar   = threading.Event()
        self._pausado = threading.Event()   # set = pausado, clear = ativo
        self._thread: threading.Thread | None = None
        self._verificar()

    # ── Inicialização ──────────────────────────────────────────────────────────
    def _verificar(self):
        try:
            subprocess.run(["hackrf_sweep", "--help"],
                           capture_output=True, timeout=3)
            self.disponivel = True
            print("  📡  hackrf_sweep disponível — scanner de espectro pronto")
        except FileNotFoundError:
            print("  ⚠   hackrf_sweep não encontrado")
        except Exception:
            self.disponivel = True  # --help retorna exit 1, mas existe

    def iniciar(self):
        if not self.disponivel:
            return
        self._parar.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="spectrum-scanner"
        )
        self._thread.start()

    def parar(self):
        self._parar.set()

    def pausar(self):
        self._pausado.set()

    def retomar(self):
        self._pausado.clear()

    # ── Varredura de banda ─────────────────────────────────────────────────────
    def _varrer(self, f_min: int, f_max: int, bin_hz: int) -> list[tuple[float, float]]:
        """Roda hackrf_sweep e retorna [(freq_mhz, dbm), ...]."""
        try:
            proc = subprocess.run(
                ["hackrf_sweep",
                 "-f", f"{f_min}:{f_max}",
                 "-l", "32",     # LNA
                 "-g", "40",     # VGA
                 "-w", str(bin_hz),
                 "-N", "1",      # uma varredura
                 "-r", "-"],     # saída stdout
                capture_output=True, text=True, timeout=12
            )
            if proc.returncode != 0 and "No HackRF" in proc.stderr:
                self.conectado = False
                return []

            pontos = []
            for linha in proc.stdout.strip().split("\n"):
                partes = linha.split(",")
                if len(partes) < 7:
                    continue
                try:
                    hz_low  = float(partes[2].strip())
                    hz_bin  = float(partes[4].strip())
                    dbs     = [float(x.strip()) for x in partes[6:]
                               if x.strip() not in ("", "nan", "-inf")]
                    for i, db in enumerate(dbs):
                        freq_mhz = (hz_low + (i + 0.5) * hz_bin) / 1e6
                        if f_min <= freq_mhz <= f_max + hz_bin / 1e6:
                            pontos.append((round(freq_mhz, 3), round(db, 1)))
                except (ValueError, IndexError):
                    continue
            return sorted(pontos, key=lambda x: x[0])
        except subprocess.TimeoutExpired:
            return []
        except Exception:
            return []

    # ── Detecção de anomalias ──────────────────────────────────────────────────
    def _anomalias(self, esp: list) -> list[dict]:
        if self._baseline_n < 3:
            return []
        anomalos = []
        for freq, dbm in esp:
            base = self._baseline.get(freq)
            if base is not None and dbm - base >= THR_ANOMALIA:
                anomalos.append({
                    "freq_mhz": freq,
                    "dbm":      dbm,
                    "delta_db": round(dbm - base, 1),
                })
        return sorted(anomalos, key=lambda x: -x["delta_db"])[:10]

    # ── Detecção FM ───────────────────────────────────────────────────────────
    def _detectar_fm(self, esp_fm: list) -> list[dict]:
        if len(esp_fm) < 5:
            return []
        dbs   = np.array([d for _, d in esp_fm])
        freqs = np.array([f for f, _ in esp_fm])
        thr   = max(float(np.percentile(dbs, 70)), -82.0)
        estacoes = []
        for i in range(2, len(dbs) - 2):
            # Pico local: maior que vizinhos num raio de 2 bins
            if (dbs[i] >= thr
                    and dbs[i] > dbs[i-1] and dbs[i] > dbs[i+1]
                    and dbs[i] > dbs[i-2] and dbs[i] > dbs[i+2]):
                freq = float(freqs[i])
                # Arredonda para .1 MHz para lookup
                freq_round = round(freq * 10) / 10
                nome = _FM_NOMES.get(freq_round, "")
                estacoes.append({
                    "freq_mhz": round(freq, 1),
                    "dbm":      round(float(dbs[i]), 1),
                    "nome":     nome,
                })
        return sorted(estacoes, key=lambda x: -x["dbm"])[:12]

    # ── Loop principal ─────────────────────────────────────────────────────────
    def _loop(self):
        while not self._parar.is_set():
            if self._pausado.is_set():
                time.sleep(0.5)
                continue
            # Hot-plug check
            r = subprocess.run(
                ["hackrf_info"], capture_output=True, text=True, timeout=3
            )
            det = "Serial number" in r.stdout or "Found HackRF" in r.stdout
            if not det:
                if self.conectado:
                    print("  📡  HackRF desconectado — scanner pausado")
                    self.conectado = False
                time.sleep(3)
                continue
            if not self.conectado:
                print("  📡  Scanner de espectro iniciando…")
                self.conectado = True

            # Adquire HackRF para toda a sessão de varredura (FM + wideband)
            if not hackrf_resource.acquire('espectro', timeout=10.0):
                time.sleep(2.0)
                continue
            try:
                esp_fm   = self._varrer(88,  108, BIN_FM)
                esp_wide = self._varrer(108, 900, BIN_WIDE)
            finally:
                hackrf_resource.release()

            espectro = esp_fm + esp_wide

            if espectro:
                # Atualiza baseline lentamente (100 ciclos = ~8 min)
                alpha = 0.08 if self._baseline_n < 10 else 0.02
                for freq, dbm in espectro:
                    if freq not in self._baseline:
                        self._baseline[freq] = dbm
                    else:
                        self._baseline[freq] = (alpha * dbm
                                                + (1 - alpha) * self._baseline[freq])
                self._baseline_n += 1

                anomalos   = self._anomalias(espectro)
                sinais_fm  = self._detectar_fm(esp_fm)

                with self._lock:
                    self.espectro         = espectro
                    # Waterfall: guarda só o dBm por ponto, decimado
                    snap = [d for _, d in espectro[::2]]
                    self.historico.append(snap)
                    self.sinais_anomalos  = anomalos
                    self.sinais_fm        = sinais_fm

            time.sleep(INTERVALO)

    # ── Estado ────────────────────────────────────────────────────────────────
    def estado(self) -> dict:
        with self._lock:
            esp = self.espectro
            freqs = [f for f, _ in esp[::3]]   # 1 em 3 para WS
            dbs   = [d for _, d in esp[::3]]
            hist  = [h for h in list(self.historico)[-20:]]
            return {
                "disponivel":  self.disponivel,
                "conectado":   self.conectado,
                "n_pontos":    len(esp),
                "freqs":       freqs,
                "dbs":         dbs,
                "historico":   hist,
                "anomalos":    self.sinais_anomalos,
                "fm":          self.sinais_fm,
                "baseline_ok": self._baseline_n >= 3,
            }


# ─── Teste ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import json
    print("\n📡 SinalRF — Teste Scanner Espectro\n")
    s = ScannerEspectro()
    if s.disponivel:
        s.iniciar()
        print("Escaneando… (Ctrl+C para parar)\n")
        try:
            while True:
                time.sleep(6)
                e = s.estado()
                print(f"  pontos={e['n_pontos']}  FM={len(e['fm'])}  anomalos={len(e['anomalos'])}")
                for est in e["fm"][:5]:
                    nome = f" ({est['nome']})" if est["nome"] else ""
                    print(f"    📻 {est['freq_mhz']} MHz  {est['dbm']} dBm{nome}")
                for sig in e["anomalos"][:3]:
                    print(f"    ⚡ {sig['freq_mhz']} MHz  +{sig['delta_db']} dB acima baseline")
        except KeyboardInterrupt:
            s.parar()
    else:
        print("hackrf_sweep não disponível.")
