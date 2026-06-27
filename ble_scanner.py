"""
mtzRF — Localizador BLE (Bluetooth Low Energy)

Encontra dispositivos que as pessoas carregam (celular, relógio, pulseira, fones)
e mede o RSSI ao vivo para localização física por gradiente "quente/frio" —
a mesma ideia da Sonda near-field, mas usando o rádio Bluetooth do PC.

Por que isso (e não "radar Doppler"):
  • O HackRF One é half-duplex (1 canal) → uma antena fixa não dá DIREÇÃO.
    Não há como mapear onde um corpo está parado só pela potência.
  • O caminho realista é achar o DISPOSITIVO da pessoa e caminhar até ele,
    seguindo o RSSI subir (esquentar). É o que TSCM faz na prática.

Características:
  • Nativo no Windows via WinRT (bleak) — NÃO usa hackrf_resource.
    Pode rodar 24/7 em paralelo com qualquer atividade do HackRF.
  • Honestidade: celulares modernos usam MAC aleatório (privacidade) — não dá
    para identificar a MESMA pessoa entre sessões, mas o MAC é estável DURANTE
    a sessão, então "achar o aparelho agora, nesta sala" funciona.
  • A distância é uma ESTIMATIVA GROSSEIRA (log-distance path loss); serve só
    para ordenar "mais perto / mais longe", não como medida métrica.
"""

import asyncio
import threading
import time
from collections import deque

try:
    from bleak import BleakScanner
    _BLEAK_OK = True
    _BLEAK_ERRO = None
except Exception as e:  # pragma: no cover - ambiente sem bleak
    _BLEAK_OK = False
    _BLEAK_ERRO = f"{type(e).__name__}: {e}"

# ── Company IDs BLE mais comuns (manufacturer_data) ──────────────────────────
FABRICANTES = {
    0x004C: "Apple", 0x0006: "Microsoft", 0x0075: "Samsung", 0x00E0: "Google",
    0x0087: "Garmin", 0x0157: "Huawei", 0x038F: "Xiaomi", 0x0501: "Fitbit",
    0x0059: "Nordic", 0x000F: "Broadcom", 0x004F: "BeoPlay", 0x0131: "Cypress",
    0x0099: "Plantronics", 0x00D2: "Bose", 0x05A7: "Sonos", 0x0171: "Amazon",
    0x0001: "Ericsson", 0x008A: "JBL/Harman", 0x02E5: "Realme/OPPO",
}

HIST_N      = 80      # histórico de RSSI (~16 s @ 5 Hz de leitura na UI)
IDADE_MAX_S = 25.0    # remove da lista quem não aparece há mais de X s
TX_POWER_1M = -59.0   # dBm de referência a 1 m (típico p/ BLE)
PATH_LOSS_N = 2.5     # expoente de perda de caminho (indoor ~2.0–3.0)


def _palpite_tipo(nome: str, fab: str | None) -> str:
    """Adivinha o tipo de aparelho pelo nome anunciado (heurística)."""
    n = (nome or "").lower()
    if any(k in n for k in ("watch", "band", "fit", "garmin", "amazfit", "pulse")):
        return "relógio/pulseira"
    if any(k in n for k in ("buds", "airpods", "headphone", "fone", "wh-", "wf-",
                            "jbl", "bose", "earbud", "headset")):
        return "fone"
    if any(k in n for k in ("phone", "galaxy", "iphone", "redmi", "moto", "pixel",
                            "poco", "oneplus")):
        return "celular"
    if any(k in n for k in ("tv", "bravia", "webos", "roku", "chromecast")):
        return "TV/mídia"
    if any(k in n for k in ("mi ", "tag", "beacon", "tile")):
        return "tag/beacon"
    if fab in ("Apple", "Samsung", "Google", "Xiaomi", "Huawei"):
        return "aparelho pessoal"
    return "—"


def _dist_estimada(rssi: float | None) -> float | None:
    """Estimativa GROSSEIRA de distância (m) por log-distance path loss."""
    if rssi is None or rssi == 0:
        return None
    d = 10 ** ((TX_POWER_1M - rssi) / (10 * PATH_LOSS_N))
    return round(min(d, 999.0), 1)


class LocalizadorBLE:
    """Scanner BLE contínuo com RSSI ao vivo para localização por gradiente."""

    def __init__(self):
        self.disponivel = _BLEAK_OK
        self.erro       = _BLEAK_ERRO
        self.alvo       = None            # MAC selecionado para localizar

        self._disp: dict[str, dict] = {}  # mac -> registro
        self._lock   = threading.Lock()
        self._parar  = False
        self._rodando = False
        self._thread = None

    # ── Callback de cada propaganda BLE recebida ────────────────────────────
    def _callback(self, dev, adv):
        try:
            rssi = adv.rssi
        except Exception:
            rssi = None
        mac  = getattr(dev, "address", None)
        if not mac:
            return
        nome = (getattr(adv, "local_name", None) or getattr(dev, "name", None) or "")

        fab = None
        try:
            md = getattr(adv, "manufacturer_data", None) or {}
            if md:
                cid = next(iter(md))
                fab = FABRICANTES.get(cid, f"0x{cid:04X}")
        except Exception:
            pass

        agora = time.time()
        with self._lock:
            d = self._disp.get(mac)
            if d is None:
                d = {
                    "mac": mac, "nome": nome, "fab": fab,
                    "tipo": _palpite_tipo(nome, fab),
                    "rssi": rssi if rssi else None,
                    "rssi_s": float(rssi) if rssi else None,
                    "rssi_max": rssi if rssi else None,
                    "hist": deque(maxlen=HIST_N),
                    "t0": agora, "tn": agora, "n": 0,
                }
                self._disp[mac] = d
            else:
                if nome and not d["nome"]:
                    d["nome"] = nome
                    d["tipo"] = _palpite_tipo(nome, d["fab"])
                if fab and not d["fab"]:
                    d["fab"] = fab
                    if d["tipo"] == "—":
                        d["tipo"] = _palpite_tipo(d["nome"], fab)

            if rssi is not None and rssi != 0:
                d["rssi"] = rssi
                d["rssi_s"] = float(rssi) if d["rssi_s"] is None \
                    else round(0.4 * rssi + 0.6 * d["rssi_s"], 1)
                d["rssi_max"] = rssi if d["rssi_max"] is None else max(d["rssi_max"], rssi)
                d["hist"].append((agora, rssi))
            d["tn"] = agora
            d["n"] += 1

    # ── Loop assíncrono do scanner (roda na thread própria) ─────────────────
    async def _run(self):
        scanner = None
        try:
            scanner = BleakScanner(detection_callback=self._callback)
            await scanner.start()
            self._rodando = True
            self.erro = None
            while not self._parar:
                await asyncio.sleep(0.3)
        except Exception as e:
            self.erro = f"{type(e).__name__}: {e}"
        finally:
            try:
                if scanner:
                    await scanner.stop()
            except Exception:
                pass
            self._rodando = False

    def _thread_main(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._run())
        finally:
            try:
                loop.close()
            except Exception:
                pass

    # ── Controle ────────────────────────────────────────────────────────────
    def iniciar(self) -> bool:
        if not self.disponivel:
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._parar = False
        self._thread = threading.Thread(target=self._thread_main, daemon=True,
                                        name="ble-scan")
        self._thread.start()
        return True

    def parar(self):
        self._parar = True

    def selecionar(self, mac: str | None):
        self.alvo = mac or None

    def limpar(self):
        with self._lock:
            self._disp.clear()

    # ── Estado para a UI ─────────────────────────────────────────────────────
    def estado(self) -> dict:
        agora = time.time()
        with self._lock:
            # remove sumidos
            for mac in [m for m, d in self._disp.items()
                        if agora - d["tn"] > IDADE_MAX_S]:
                self._disp.pop(mac, None)
                if self.alvo == mac:
                    self.alvo = None

            lista = []
            for d in self._disp.values():
                hist = list(d["hist"])
                # tendência: média recente − média anterior (positiva = esquentando)
                trend = 0.0
                if len(hist) >= 6:
                    rec = [r for _, r in hist[-3:]]
                    ant = [r for _, r in hist[-6:-3]]
                    trend = (sum(rec) / len(rec)) - (sum(ant) / len(ant))
                lista.append({
                    "mac": d["mac"],
                    "nome": d["nome"] or "(sem nome)",
                    "fab": d["fab"],
                    "tipo": d["tipo"],
                    "rssi": d["rssi"],
                    "rssi_s": d["rssi_s"],
                    "rssi_max": d["rssi_max"],
                    "dist_m": _dist_estimada(d["rssi_s"]),
                    "trend": round(trend, 1),
                    "n": d["n"],
                    "idade": round(agora - d["tn"], 1),
                    "hist": [r for _, r in hist[-40:]],
                    "alvo": d["mac"] == self.alvo,
                })
            # ordena: alvo primeiro, depois por sinal mais forte
            lista.sort(key=lambda x: (not x["alvo"],
                                      -(x["rssi_s"] if x["rssi_s"] is not None else -999)))
            return {
                "disponivel": self.disponivel,
                "rodando": self._rodando,
                "erro": self.erro,
                "alvo": self.alvo,
                "n": len(lista),
                "ts": round(agora, 2),
                "dispositivos": lista,
            }


# ── Teste manual ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    loc = LocalizadorBLE()
    if not loc.iniciar():
        print("BLE indisponível:", loc.erro)
        raise SystemExit(1)
    print("Escaneando BLE… Ctrl+C para sair\n")
    try:
        while True:
            time.sleep(2)
            e = loc.estado()
            print(f"\n[{e['n']} dispositivos]  rodando={e['rodando']}")
            for d in e["dispositivos"][:10]:
                seta = "▲" if d["trend"] > 1 else ("▼" if d["trend"] < -1 else "•")
                print(f"  {seta} {str(d['rssi_s']):>6} dBm  ~{d['dist_m']}m  "
                      f"{d['tipo']:<16} {d['nome']}  [{d['fab'] or '?'}]")
    except KeyboardInterrupt:
        loc.parar()
