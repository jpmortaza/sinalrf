"""
mtzRF — Scanner de Inteligência Espectral
Varre 1 MHz – 6 GHz com hackrf_sweep.
Descobre, classifica e rastreia sinais automaticamente.
Persistência, baseline adaptativo, feed de descobertas.
"""

import subprocess
import threading
import time
import numpy as np
from collections import deque
from typing import Optional
import hackrf_resource

# ── Configuração ────────────────────────────────────────────────────────────
FREQ_MIN_MHZ     = 88        # começa no FM (evita ruído de baixa frequência)
FREQ_MAX_MHZ     = 6000      # limite superior do HackRF
BIN_HZ           = 1_000_000 # 1 MHz/bin → ~5912 bins
GANHO_LNA        = 32
GANHO_VGA        = 40
INTERVALO_SWEEP  = 12.0      # segundos entre passagens (deixa outros módulos usarem HackRF)
HIST_MAX         = 15        # sweeps para calcular baseline mediana
LIMIAR_ABS_DBM   = -75.0     # threshold absoluto: bins abaixo disso ignorados
MIN_SWEEPS_ATIVO = 1         # mínimo de sweeps ativos para entrar na lista
PANORAMA_PTS     = 1200      # pontos downsampled para o canvas
FEED_MAX         = 80        # tamanho máximo do feed de descobertas

# ── Base de dados de bandas ──────────────────────────────────────────────────
BANDAS = [
    # (min_hz, max_hz, categoria, cor_hex, descricao)
    (    530_000,   1_705_000, "AM",        "#AA8833", "AM Broadcast"),
    ( 87_500_000, 108_000_000, "FM",        "#47DF7F", "FM Broadcast"),
    (108_000_000, 137_000_000, "AVIAÇÃO",   "#4FAFFF", "Aviação Civil VHF"),
    (137_000_000, 138_000_000, "SAT-MET",   "#CC44FF", "Satélite Meteorológico"),
    (138_000_000, 156_000_000, "VHF-MIL",   "#FF8833", "Militar / PMR"),
    (156_000_000, 174_000_000, "MARÍTIMO",  "#00CCFF", "Marítimo VHF"),
    (162_400_000, 162_550_000, "NOAA",      "#44FFAA", "NOAA Weather Radio"),
    (174_000_000, 230_000_000, "DAB",       "#FF44CC", "Rádio Digital DAB"),
    (230_000_000, 406_000_000, "VHF-UHF",   "#888888", "VHF/UHF Diverso"),
    (406_000_000, 432_000_000, "UHF-PROF",  "#FF8C33", "UHF Profissional"),
    (433_050_000, 434_790_000, "ISM-433",   "#FFD700", "ISM 433 MHz / LoRa / IoT"),
    (434_790_000, 462_000_000, "UHF-PROF",  "#FF8C33", "UHF Profissional"),
    (462_000_000, 467_000_000, "FRS",       "#88FF44", "FRS / GMRS Rádio"),
    (467_000_000, 698_000_000, "UHF-PROF",  "#FF8C33", "UHF Profissional / TV"),
    (698_000_000, 800_000_000, "CELULAR",   "#FF4444", "Celular 700 MHz LTE"),
    (800_000_000, 900_000_000, "CELULAR",   "#FF4444", "Celular 850 MHz"),
    (863_000_000, 870_000_000, "ISM-868",   "#FFD700", "ISM 868 MHz / LoRa EU"),
    (900_000_000, 960_000_000, "CELULAR",   "#FF4444", "Celular 900 MHz GSM"),
    (960_000_000,1_215_000_000,"AERONAV",   "#4FAFFF", "Radionavegação Aérea"),
    (1_164_000_000,1_300_000_000,"GNSS",    "#00FF88", "GPS / GNSS L2/L5"),
    (1_452_000_000,1_492_000_000,"DAB-L",   "#FF44CC", "DAB+ Banda L"),
    (1_559_000_000,1_610_000_000,"GNSS",    "#00FF88", "GPS / GLONASS / Galileo L1"),
    (1_700_000_000,1_900_000_000,"CELULAR", "#FF4444", "Celular 4G 1.7-1.9 GHz"),
    (1_900_000_000,2_100_000_000,"CELULAR", "#FF4444", "Celular 4G AWS / 2100"),
    (2_100_000_000,2_170_000_000,"CELULAR", "#FF4444", "Celular 4G 2100 MHz"),
    (2_400_000_000,2_483_500_000,"WiFi-2G", "#4FAFFF", "WiFi 2.4 GHz / Bluetooth"),
    (2_483_500_000,2_500_000_000,"ISM-2G",  "#FFD700", "ISM 2.4 GHz"),
    (2_500_000_000,2_690_000_000,"CELULAR", "#FF4444", "Celular 4G LTE 2.5 GHz"),
    (3_300_000_000,3_800_000_000,"5G",      "#FF44FF", "5G NR Banda n77/n78"),
    (5_150_000_000,5_350_000_000,"WiFi-5G", "#4FAFFF", "WiFi 5 GHz Banda A"),
    (5_470_000_000,5_850_000_000,"WiFi-5G", "#4FAFFF", "WiFi 5 GHz Banda C/E"),
]

def _classificar(freq_hz: float) -> tuple[str, str, str]:
    for mn, mx, cat, cor, desc in BANDAS:
        if mn <= freq_hz <= mx:
            return cat, cor, desc
    return "DESCONHECIDO", "#555555", "Não classificado"


class ScannerInteligente:
    """
    Varre o espectro completo do HackRF (88 MHz – 6 GHz) continuamente.
    Descobre e rastreia milhares de sinais automaticamente.
    """

    def __init__(self):
        self.disponivel      = False
        self.conectado       = False
        self.sweeps          = 0
        self.duracao_sweep   = 0.0
        self._ultimo_ts      = 0.0
        self._sweep_em_curso = False

        # Arrays numpy (inicializados no primeiro sweep)
        self._freqs_hz:  Optional[np.ndarray] = None
        self._dbm_cur:   Optional[np.ndarray] = None
        self._dbm_base:  Optional[np.ndarray] = None
        self._dbm_hist:  list[np.ndarray]     = []
        self._n_ativo:   Optional[np.ndarray] = None   # contagem por bin

        # Sinais descobertos: {bin_idx → dict}
        self._sinais:     dict[int, dict] = {}
        self._descobertos = deque(maxlen=FEED_MAX)

        self._lock    = threading.Lock()
        self._parar   = threading.Event()
        self._pausado = threading.Event()   # set = pausado, clear = ativo
        self._thread: Optional[threading.Thread] = None

        self._verificar()

    # ── Inicialização ──────────────────────────────────────────────────────────
    def _verificar(self):
        try:
            r = subprocess.run(["hackrf_info"], capture_output=True, text=True, timeout=3)
            self.disponivel = True
            self.conectado  = ("Serial number" in r.stdout or "Found HackRF" in r.stdout)
            if self.conectado:
                print("  🌌  Intel Scanner: HackRF detectado — escaneando 88 MHz – 6 GHz")
        except FileNotFoundError:
            print("  ⚠   Intel Scanner: hackrf_info não encontrado")
        except Exception as e:
            print(f"  ⚠   Intel Scanner: {e}")

    def iniciar(self):
        if not self.disponivel:
            return
        self._parar.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="intel-scanner"
        )
        self._thread.start()

    def parar(self):
        self._parar.set()

    def pausar(self):
        self._pausado.set()

    def retomar(self):
        self._pausado.clear()

    # ── Loop principal ─────────────────────────────────────────────────────────
    def _loop(self):
        while not self._parar.is_set():
            # Hot-plug
            try:
                r = subprocess.run(["hackrf_info"], capture_output=True, text=True, timeout=3)
                ok = ("Serial number" in r.stdout or "Found HackRF" in r.stdout)
                if ok and not self.conectado:
                    print("  🌌  Intel Scanner: HackRF conectado")
                    self.conectado = True
                elif not ok and self.conectado:
                    print("  🌌  Intel Scanner: HackRF desconectado")
                    self.conectado = False
                    with self._lock:
                        self._freqs_hz = None
            except Exception:
                pass

            if not self.conectado or self._pausado.is_set():
                self._parar.wait(4.0)
                continue

            # Adquire HackRF exclusivamente para toda a passagem (até 120s)
            if not hackrf_resource.acquire('intel', timeout=15.0):
                self._parar.wait(INTERVALO_SWEEP / 2)
                continue

            self._sweep_em_curso = True
            try:
                esp = self._varrer()
            finally:
                self._sweep_em_curso = False
                hackrf_resource.release()

            if esp is not None:
                self._atualizar(esp)
            else:
                # Falhou (HackRF ocupado por outro módulo) — esperar e tentar de novo
                self._parar.wait(INTERVALO_SWEEP / 2)
                continue

            self._parar.wait(INTERVALO_SWEEP)

    # ── Captura hackrf_sweep ───────────────────────────────────────────────────
    def _varrer(self) -> Optional[dict]:
        """Executa hackrf_sweep no range completo e parseia o CSV."""
        t0 = time.time()
        try:
            proc = subprocess.run(
                ["hackrf_sweep",
                 "-f", f"{FREQ_MIN_MHZ}:{FREQ_MAX_MHZ}",
                 "-l", str(GANHO_LNA),
                 "-g", str(GANHO_VGA),
                 "-w", str(BIN_HZ),
                 "-N", "1",   # 1 passagem completa
                 "-r", "-"],  # saída no stdout
                capture_output=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            return None
        except Exception:
            return None

        self.duracao_sweep = round(time.time() - t0, 1)

        if not proc.stdout or len(proc.stdout) < 200:
            return None

        freqs_hz = []
        dbm_vals = []

        for linha in proc.stdout.decode("utf-8", errors="ignore").splitlines():
            partes = linha.strip().split(",")
            if len(partes) < 7:
                continue
            try:
                hz_low  = float(partes[2])
                hz_high = float(partes[3])
                bin_w   = float(partes[4])
                vals    = [float(v) for v in partes[6:] if v.strip()]
                if not vals:
                    continue
                for i, db in enumerate(vals):
                    freq = hz_low + bin_w * (i + 0.5)
                    if hz_low <= freq < hz_high:
                        freqs_hz.append(freq)
                        dbm_vals.append(db)
            except (ValueError, IndexError):
                continue

        if len(freqs_hz) < 100:
            return None

        # Ordenar por frequência
        arr_f = np.array(freqs_hz, dtype=float)
        arr_d = np.array(dbm_vals, dtype=float)
        idx   = np.argsort(arr_f)
        arr_f, arr_d = arr_f[idx], arr_d[idx]

        # Deduplicar (média por MHz inteiro)
        freqs_u = np.round(arr_f, -6)
        u_vals, inv = np.unique(freqs_u, return_inverse=True)
        dbm_u   = np.zeros(len(u_vals))
        cnt_u   = np.zeros(len(u_vals))
        for i, d in enumerate(arr_d):
            dbm_u[inv[i]] += d
            cnt_u[inv[i]] += 1
        dbm_u /= np.maximum(cnt_u, 1)

        return {"freqs": u_vals, "dbm": dbm_u}

    # ── Atualização de inteligência ────────────────────────────────────────────
    def _atualizar(self, esp: dict):
        freqs = esp["freqs"]
        dbm   = esp["dbm"]
        n     = len(freqs)

        with self._lock:
            # Redimensiona se necessário
            if self._freqs_hz is None or len(self._freqs_hz) != n:
                self._freqs_hz = freqs
                self._dbm_cur  = dbm.copy()
                self._n_ativo  = np.zeros(n, dtype=int)
                self._dbm_hist = []
                self._sinais   = {}

            self._dbm_cur = dbm.copy()

            # Histórico para visualização do baseline (ruído de fundo)
            self._dbm_hist.append(dbm.copy())
            if len(self._dbm_hist) > HIST_MAX:
                self._dbm_hist.pop(0)

            if len(self._dbm_hist) >= 3:
                hist_arr       = np.stack(self._dbm_hist, axis=0)
                self._dbm_base = np.percentile(hist_arr, 15, axis=0)  # percentil baixo = noise floor
            else:
                self._dbm_base = dbm.copy() - 10.0

            # Bins ativos: dBm acima do threshold absoluto
            acima = dbm >= LIMIAR_ABS_DBM
            self._n_ativo += acima.astype(int)

            self.sweeps     += 1
            self._ultimo_ts  = time.time()

            # Atualiza sinais descobertos
            self._descobrir_picos(freqs, dbm, acima)

    def _descobrir_picos(self, freqs, dbm, acima):
        """
        Encontra picos locais com dBm >= LIMIAR_ABS_DBM.
        Um pico é máximo local em janela de ±3 bins.
        """
        n   = len(freqs)
        jan = 3

        for i in range(jan, n - jan):
            if not acima[i]:
                continue
            # Pico local: maior que todos os vizinhos
            vizinhos = dbm[i - jan: i + jan + 1]
            if float(dbm[i]) < float(vizinhos.max()) - 0.01:
                continue

            freq_hz  = float(freqs[i])
            dbm_v    = float(dbm[i])
            delta_v  = dbm_v - float(self._dbm_base[i]) if self._dbm_base is not None else 0.0
            cat, cor, desc = _classificar(freq_hz)

            is_novo = i not in self._sinais
            if is_novo:
                self._sinais[i] = {
                    "freq_hz":      freq_hz,
                    "freq_mhz":     round(freq_hz / 1e6, 3),
                    "dbm":          dbm_v,
                    "dbm_max":      dbm_v,
                    "delta_db":     delta_v,
                    "n_ativo":      1,
                    "cat":          cat,
                    "cor":          cor,
                    "desc":         desc,
                    "ts_primeiro":  self._ultimo_ts,
                    "ts_ultimo":    self._ultimo_ts,
                    "sweep_desc":   self.sweeps,
                }
                # Adiciona ao feed apenas depois do 2º sweep
                if self.sweeps >= 2:
                    self._descobertos.appendleft({
                        "freq_mhz": round(freq_hz / 1e6, 3),
                        "dbm":      round(dbm_v, 1),
                        "delta":    round(delta_v, 1),
                        "cat":      cat,
                        "cor":      cor,
                        "desc":     desc,
                        "ts":       self._ultimo_ts,
                    })
            else:
                s = self._sinais[i]
                s["dbm"]      = dbm_v
                s["dbm_max"]  = max(s["dbm_max"], dbm_v)
                s["delta_db"] = delta_v
                s["n_ativo"] += 1
                s["ts_ultimo"] = self._ultimo_ts

    # ── Espectro bruto (para a Sentinela) ──────────────────────────────────────
    def espectro_bruto(self) -> Optional[dict]:
        """Retorna o espectro completo atual (não-downsampled) para baseline/diff.
        { sweeps, freqs_hz[np], dbm[np], base[np] } ou None se ainda não varreu."""
        with self._lock:
            if self._freqs_hz is None or self._dbm_cur is None:
                return None
            return {
                "sweeps":   self.sweeps,
                "ts":       self._ultimo_ts,
                "freqs_hz": self._freqs_hz.copy(),
                "dbm":      self._dbm_cur.copy(),
                "base":     (self._dbm_base.copy() if self._dbm_base is not None else self._dbm_cur.copy()),
            }

    # ── Saída de dados ─────────────────────────────────────────────────────────
    def inteligencia(self) -> dict:
        with self._lock:
            base_resp = {
                "disponivel":      self.disponivel,
                "conectado":       self.conectado,
                "sweeps":          self.sweeps,
                "duracao_sweep":   self.duracao_sweep,
                "sweep_em_curso":  self._sweep_em_curso,
                "ultimo_ts":       self._ultimo_ts,
                "n_bins":          len(self._freqs_hz) if self._freqs_hz is not None else 0,
            }

            if self._freqs_hz is None:
                return {
                    **base_resp,
                    "panorama":   {"freqs": [], "dbm": [], "baseline": []},
                    "sinais":     [],
                    "categorias": {},
                    "descobertos": [],
                    "n_ativos":   0,
                }

            n    = len(self._freqs_hz)
            base = self._dbm_base if self._dbm_base is not None else self._dbm_cur

            # Panorama downsampled para o canvas
            pts   = min(PANORAMA_PTS, n)
            idx   = np.round(np.linspace(0, n - 1, pts)).astype(int)
            pan_f = [round(float(self._freqs_hz[i]) / 1e6, 2) for i in idx]
            pan_d = [round(float(self._dbm_cur[i]), 1) for i in idx]
            pan_b = [round(float(base[i]), 1)          for i in idx]

            # Lista de sinais descobertos
            sinais = []
            for i, s in self._sinais.items():
                persist = s["n_ativo"] / max(self.sweeps, 1)
                if s["n_ativo"] < MIN_SWEEPS_ATIVO:
                    continue
                sinais.append({
                    "freq_mhz":    s["freq_mhz"],
                    "dbm":         round(s["dbm"], 1),
                    "dbm_max":     round(s["dbm_max"], 1),
                    "delta_db":    round(s["delta_db"], 1),
                    "persistencia": round(persist, 3),
                    "n_ativo":     s["n_ativo"],
                    "cat":         s["cat"],
                    "cor":         s["cor"],
                    "desc":        s["desc"],
                    "novo":        (self.sweeps - s["sweep_desc"]) < 4,
                })
            sinais.sort(key=lambda x: x["dbm"], reverse=True)

            # Estatísticas por categoria
            cats: dict[str, dict] = {}
            for s in sinais:
                c = s["cat"]
                if c not in cats:
                    cats[c] = {"n": 0, "dbm_max": -130.0, "cor": s["cor"], "desc": s["desc"]}
                cats[c]["n"]       += 1
                cats[c]["dbm_max"]  = max(cats[c]["dbm_max"], s["dbm"])

            return {
                **base_resp,
                "freq_min_mhz": round(float(self._freqs_hz[0])  / 1e6, 1),
                "freq_max_mhz": round(float(self._freqs_hz[-1]) / 1e6, 1),
                "panorama": {
                    "freqs":    pan_f,
                    "dbm":      pan_d,
                    "baseline": pan_b,
                },
                "sinais":     sinais,
                "categorias": cats,
                "descobertos": list(self._descobertos),
                "n_ativos":   len(sinais),
            }


# ── Teste independente ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n🌌 mtzRF — Teste Intel Scanner\n")
    s = ScannerInteligente()
    if s.conectado:
        s.iniciar()
        print("Escaneando 88 MHz – 6 GHz… (Ctrl+C para parar)\n")
        try:
            while True:
                time.sleep(15)
                d = s.inteligencia()
                print(f"  Sweeps: {d['sweeps']}  Bins: {d['n_bins']}  "
                      f"Sinais: {d['n_ativos']}  Duração: {d['duracao_sweep']}s")
                for sig in d['sinais'][:5]:
                    print(f"    {sig['freq_mhz']:8.3f} MHz  {sig['dbm']:6.1f} dBm  "
                          f"persist={sig['persistencia']:.2f}  [{sig['cat']}] {sig['desc']}")
        except KeyboardInterrupt:
            s.parar()
    else:
        print("HackRF não conectado.")
