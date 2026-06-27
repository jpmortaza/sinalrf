#!/usr/bin/env python3
"""
mtzRF — Monitor de interferência GPS (DEFENSIVO)
=================================================
Recepção PASSIVA da banda GPS L1 (1575,42 MHz) com o HackRF para detectar jamming
e anomalias de RF. É uma ferramenta de inteligência defensiva — NÃO transmite nada.

O sinal GPS chega ~20 dB ABAIXO do ruído térmico, então uma banda L1 saudável parece
um "chão de ruído" plano. Qualquer energia forte e estruturada ali é anômala:
  • pico estreito forte  -> jammer CW ("GPS blocker" de isqueiro, o mais comum)
  • banda toda elevada   -> jammer de ruído ou possível spoofer (transmite GPS forte)

Limite honesto: o HackRF é um front-end SDR, não um receptor GPS. Ele detecta
INTERFERÊNCIA de RF (jamming/energia anômala). Detecção de spoofing por consistência
de posição/tempo exige um receptor GPS (NMEA) — fora do escopo deste módulo.
"""

import os
import tempfile
import subprocess

import numpy as np

import hackrf_resource

L1_HZ = 1_575_420_000
_baseline = {"floor_db": None, "band_db": None}


def _capturar(sr: int, dur_s: float, lna: int, vga: int, amp: bool):
    n = int(sr * dur_s)
    tmp = tempfile.NamedTemporaryFile(suffix=".iq", delete=False)
    tmp.close()
    cmd = ["hackrf_transfer", "-r", tmp.name, "-f", str(L1_HZ),
           "-s", str(int(sr)), "-g", str(vga), "-l", str(lna), "-n", str(n)]
    if amp:
        cmd += ["-a", "1"]
    try:
        if not hackrf_resource.acquire("gps-mon", timeout=8.0):
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
    if raw.size < 2048:
        return None
    raw = raw[:(raw.size // 2) * 2].astype(np.float32)
    return raw[0::2] + 1j * raw[1::2]


def _psd_db(iq, nfft: int = 1024):
    nseg = max(1, len(iq) // nfft)
    win = np.hanning(nfft).astype(np.float32)
    acc = np.zeros(nfft, dtype=np.float64)
    n = 0
    for k in range(nseg):
        seg = iq[k * nfft:(k + 1) * nfft]
        if len(seg) < nfft:
            break
        sp = np.fft.fftshift(np.fft.fft(seg * win))
        acc += np.abs(sp) ** 2
        n += 1
    acc /= max(1, n)
    return 10.0 * np.log10(acc + 1e-9)


def medir(sr: int = 4_000_000, dur_s: float = 0.15,
          lna: int = 40, vga: int = 24, amp: bool = True) -> dict:
    iq = _capturar(sr, dur_s, lna, vga, amp)
    if iq is None:
        return {"ok": False, "motivo": "falha na captura (HackRF ocupado ou erro)"}

    psd = _psd_db(iq, 1024)
    floor = float(np.median(psd))
    # remove o spike de DC/LO do centro (artefato do SDR, não é sinal real)
    c = len(psd) // 2
    guard = max(3, len(psd) // 80)
    psd = psd.copy()
    psd[c - guard:c + guard + 1] = floor
    pico = float(np.max(psd))
    band = float(10.0 * np.log10(np.mean(np.power(10.0, psd / 10.0))))
    idx = int(np.argmax(psd))
    off_hz = (idx - len(psd) / 2) * (sr / len(psd))
    crista = pico - floor
    picos = int(np.sum(psd > floor + 12.0))

    delta = None
    if _baseline["band_db"] is not None:
        delta = round(band - _baseline["band_db"], 1)

    status, nivel, msg = "LIMPO", 0, "Banda L1 normal (GPS fica abaixo do ruído)."
    if crista >= 25 and picos <= 8:
        status, nivel = "JAMMING", 3
        msg = f"Pico forte e estreito ({crista:.0f} dB acima do ruído, {off_hz/1e3:+.0f} kHz) — provável jammer CW."
    elif crista >= 18:
        status, nivel = "JAMMING", 3
        msg = f"Energia forte na L1 ({crista:.0f} dB acima do ruído) — interferência ativa."
    elif delta is not None and delta >= 6:
        status, nivel = "ANOMALIA", 2
        msg = f"Banda L1 {delta:+.0f} dB acima do baseline — possível jammer de ruído ou spoofer."
    elif crista >= 12:
        status, nivel = "ANOMALIA", 1
        msg = f"Sinal acima do esperado na L1 ({crista:.0f} dB) — vale investigar."

    passo = max(1, len(psd) // 256)
    espectro = [round(float(x), 1) for x in psd[::passo]]

    return {
        "ok": True, "status": status, "nivel": nivel, "msg": msg,
        "floor_db": round(floor, 1), "pico_db": round(pico, 1), "band_db": round(band, 1),
        "crista_db": round(crista, 1), "off_khz": round(off_hz / 1e3, 1),
        "picos": picos, "delta_baseline": delta,
        "tem_baseline": _baseline["band_db"] is not None,
        "sr": sr, "espectro": espectro,
    }


def calibrar(sr: int = 4_000_000) -> dict:
    r = medir(sr=sr)
    if not r.get("ok"):
        return r
    _baseline["floor_db"] = r["floor_db"]
    _baseline["band_db"] = r["band_db"]
    return {"ok": True, "floor_db": r["floor_db"], "band_db": r["band_db"]}
