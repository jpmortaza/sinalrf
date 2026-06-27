#!/usr/bin/env python3
"""
mtzRF — ADS-B 1090 MHz (radar de aviões)
=========================================
Recebe e decodifica os squitters Mode-S/ADS-B que toda aeronave transmite em
1090 MHz: ICAO, callsign, altitude, velocidade, rumo e posição (lat/lon).
Vira um radar passivo local — 100% legal (só recepção).

Pipeline: hackrf_transfer (IQ 2 Msps) -> demodulação Mode-S em numpy (detecção de
preâmbulo estilo dump1090 + PPM + CRC) -> pyModeS decodifica os campos.
"""

import os
import time
import tempfile
import threading
import subprocess

import numpy as np

import hackrf_resource

try:
    import pyModeS as pms
    PMS_OK = True
except Exception:
    PMS_OK = False

SR = 2_000_000          # 2 amostras por µs (PPM Mode-S)
F_HZ = 1_090_000_000
EXPIRA_S = 60           # remove aeronave sem update há 60s


class ADSB:
    def __init__(self):
        self.rodando = False
        self.ts = 0.0
        self.n_msgs = 0
        self.n_sweeps = 0
        self._av: dict[str, dict] = {}      # icao -> dados
        self._lock = threading.Lock()
        self._parar = threading.Event()
        self._thread = None

    # ── controle ────────────────────────────────────────────────────────────────
    def iniciar(self):
        if self.rodando or not PMS_OK:
            return
        self.rodando = True
        self._parar.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="adsb")
        self._thread.start()

    def parar(self):
        self._parar.set()
        self.rodando = False

    # ── captura ───────────────────────────────────────────────────────────────────
    def _capturar(self, dur_s: float = 1.6, lna: int = 40, vga: int = 40, amp: bool = True):
        n = int(SR * dur_s)
        tmp = tempfile.NamedTemporaryFile(suffix=".iq", delete=False)
        tmp.close()
        cmd = ["hackrf_transfer", "-r", tmp.name, "-f", str(F_HZ), "-s", str(SR),
               "-g", str(vga), "-l", str(lna), "-n", str(n)]
        if amp:
            cmd += ["-a", "1"]
        try:
            if not hackrf_resource.acquire("adsb", timeout=6.0):
                return None
            try:
                subprocess.run(cmd, capture_output=True, timeout=max(8, dur_s * 6 + 6))
            finally:
                hackrf_resource.release()
            raw = np.fromfile(tmp.name, dtype=np.int8)
        except (OSError, subprocess.TimeoutExpired):
            return None
        finally:
            try: os.unlink(tmp.name)
            except OSError: pass
        if raw.size < 5000:
            return None
        raw = raw[:(raw.size // 2) * 2].astype(np.float32)
        i = raw[0::2]; q = raw[1::2]
        return i * i + q * q          # potência (preserva ordem p/ comparações)

    # ── demodulação Mode-S ─────────────────────────────────────────────────────────
    @staticmethod
    def _demod(m: np.ndarray) -> list:
        N = m.size
        L = N - 241
        if L <= 0:
            return []
        j = lambda k: m[k:k + L]
        j0, j1, j2, j3 = j(0), j(1), j(2), j(3)
        j4, j5, j6 = j(4), j(5), j(6)
        j7, j8, j9 = j(7), j(8), j(9)
        thr = float(np.median(m)) * 6.0 + 1.0
        cond = ((j0 > j1) & (j1 < j2) & (j2 > j3) & (j3 < j0) & (j4 < j0) &
                (j5 < j0) & (j6 < j0) & (j7 > j8) & (j8 < j9) & (j9 > j6) & (j0 > thr))
        cands = np.flatnonzero(cond)
        if cands.size > 60000:        # proteção: amostra se houver excesso (ruído)
            cands = cands[::cands.size // 60000 + 1]

        msgs = []
        vistos = set()
        for c in cands:
            base = c + 16
            seg = m[base:base + 224]
            if seg.size < 224:
                continue
            bits = (seg[0::2] > seg[1::2]).astype(np.uint8)
            by = np.packbits(bits)            # 14 bytes
            if (by[0] >> 3) not in (17, 18):  # DF17/18 = ADS-B
                continue
            hexmsg = by.tobytes().hex().upper()
            if hexmsg in vistos:
                continue
            vistos.add(hexmsg)
            try:
                if pms.crc(hexmsg) == 0:
                    msgs.append(hexmsg)
            except Exception:
                continue
        return msgs

    # ── decodificação + rastreio ──────────────────────────────────────────────────
    def _decodificar(self, msgs: list):
        agora = time.time()
        with self._lock:
            for msg in msgs:
                try:
                    icao = pms.adsb.icao(msg)
                    if not icao:
                        continue
                    tc = pms.adsb.typecode(msg)
                    av = self._av.setdefault(icao, {"icao": icao, "callsign": None,
                        "alt": None, "spd": None, "trk": None, "vs": None,
                        "lat": None, "lon": None, "_oe": {}, "_oet": {}, "msgs": 0})
                    av["msgs"] += 1
                    av["last"] = agora
                    if 1 <= tc <= 4:
                        cs = pms.adsb.callsign(msg)
                        if cs:
                            av["callsign"] = cs.replace("_", "").strip()
                    elif 9 <= tc <= 18:
                        av["alt"] = pms.adsb.altitude(msg)
                        oe = pms.adsb.oe_flag(msg)
                        av["_oe"][oe] = msg
                        av["_oet"][oe] = agora
                        if 0 in av["_oe"] and 1 in av["_oe"] and abs(av["_oet"][0] - av["_oet"][1]) < 10:
                            pos = pms.adsb.position(av["_oe"][0], av["_oe"][1], av["_oet"][0], av["_oet"][1])
                            if pos:
                                av["lat"], av["lon"] = round(pos[0], 5), round(pos[1], 5)
                    elif tc == 19:
                        v = pms.adsb.velocity(msg)
                        if v:
                            av["spd"], av["trk"], av["vs"] = v[0], v[1], v[2]
                    self.n_msgs += 1
                except Exception:
                    continue
            # expira antigos
            for k in [k for k, a in self._av.items() if agora - a.get("last", 0) > EXPIRA_S]:
                self._av.pop(k, None)

    def _loop(self):
        while not self._parar.is_set():
            m = self._capturar()
            if m is not None:
                self.n_sweeps += 1
                self.ts = time.time()
                self._decodificar(self._demod(m))
            self._parar.wait(0.3)

    # ── saída ─────────────────────────────────────────────────────────────────────
    def estado(self) -> dict:
        with self._lock:
            avs = []
            for a in self._av.values():
                avs.append({k: a[k] for k in ("icao", "callsign", "alt", "spd", "trk", "vs", "lat", "lon", "msgs")})
            com_pos = [a for a in avs if a["lat"] is not None]
        avs.sort(key=lambda a: (a["lat"] is None, -(a["msgs"])))
        return {
            "ok": PMS_OK, "rodando": self.rodando, "ts": self.ts,
            "n_sweeps": self.n_sweeps, "n_msgs": self.n_msgs,
            "n_avioes": len(avs), "n_com_pos": len(com_pos),
            "avioes": avs,
        }
