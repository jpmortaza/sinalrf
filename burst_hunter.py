#!/usr/bin/env python3
"""
mtzRF — Burst Hunter (caçador de rajadas)
==========================================
Pega a classe de transmissor que escapa do sweep instantâneo: o que "dorme e
acorda" por instantes — bugs com VOX (só transmitem quando há voz), módulos GSM
em rajada, gravadores agendados. Captura um trecho de IQ de banda larga numa
janela, monta o espectrograma (tempo × frequência) e detecta sinais que aparecem
de forma INTERMITENTE (não estão sempre ligados nem são ruído).

Só recepção. Reusa o padrão de captura via hackrf_transfer.
"""

import os
import tempfile
import subprocess

import numpy as np

import hackrf_resource
from intelligence_scanner import _classificar

NFFT = 1024
T_MAX = 1500          # frames de tempo (pooling) p/ detecção + waterfall
MARGEM_DB = 10.0      # acima da mediana temporal = "ativo"
FRAC_MAX = 0.75       # ativo em > 75% do tempo = contínuo (não é rajada)
MIN_ON = 4            # frames mínimos de ON (descarta blips de ruído de 1 frame)
CONTIG_MIN = 0.5      # maior bloco contínuo / total ON — rajada é contígua no tempo
                      # (FM/broadcast espalha no tempo e é rejeitado)


def _capturar(freq_hz: int, sr: int, dur_s: float, lna: int, vga: int, amp: bool):
    n = int(sr * dur_s)
    tmp = tempfile.NamedTemporaryFile(suffix=".iq", delete=False)
    tmp.close()
    cmd = ["hackrf_transfer", "-r", tmp.name, "-f", str(freq_hz),
           "-s", str(int(sr)), "-g", str(vga), "-l", str(lna), "-n", str(n)]
    if amp:
        cmd += ["-a", "1"]
    try:
        if not hackrf_resource.acquire("burst", timeout=8.0):
            return None
        try:
            subprocess.run(cmd, capture_output=True, timeout=max(10, dur_s * 6 + 6))
        finally:
            hackrf_resource.release()
        raw = np.fromfile(tmp.name, dtype=np.int8)
    except (OSError, subprocess.TimeoutExpired):
        return None
    finally:
        try: os.unlink(tmp.name)
        except OSError: pass
    if raw.size < NFFT * 8:
        return None
    raw = raw[:(raw.size // 2) * 2].astype(np.float32)
    return raw[0::2] + 1j * raw[1::2]


def _b64(b: bytes) -> str:
    import base64
    return base64.b64encode(b).decode("ascii")


def cacar(freq_mhz: float, sr: int = 8_000_000, dur_s: float = 2.0,
          lna: int = 32, vga: int = 40, amp: bool = False) -> dict:
    freq_hz = int(freq_mhz * 1e6)
    iq = _capturar(freq_hz, sr, dur_s, lna, vga, amp)
    if iq is None:
        return {"ok": False, "motivo": "falha na captura (HackRF ocupado ou erro)"}

    nframes = len(iq) // NFFT
    if nframes < 8:
        return {"ok": False, "motivo": "captura curta demais"}
    seg = iq[:nframes * NFFT].reshape(nframes, NFFT)
    win = np.hanning(NFFT).astype(np.float32)
    sp = np.fft.fftshift(np.fft.fft(seg * win, axis=1), axes=1)
    P = 10.0 * np.log10(np.abs(sp) ** 2 + 1e-9)          # (nframes, NFFT) dBFS-ish

    # pooling no tempo (max preserva rajadas curtas) até T_MAX frames
    if nframes > T_MAX:
        fator = nframes // T_MAX
        nt = (nframes // fator) * fator
        P = P[:nt].reshape(nt // fator, fator, NFFT).max(axis=1)
    nt = P.shape[0]
    dt = (NFFT / sr) * max(1, nframes // nt)             # seg por frame pooled

    # notch do spike de DC/LO no centro
    c = NFFT // 2
    g = max(2, NFFT // 80)
    P[:, c - g:c + g + 1] = np.median(P)

    baseline_f = np.median(P, axis=0)                    # ruído por bin
    ativo = P > (baseline_f + MARGEM_DB)
    frac = ativo.mean(axis=0)                            # fração do tempo ativo (por bin)
    pico_f = P.max(axis=0)

    # bins "rajada": aparecem parte do tempo (não contínuo, não ruído)
    bursty = (frac > 0.0) & (frac <= FRAC_MAX) & (pico_f >= baseline_f + MARGEM_DB)

    # agrupa bins contíguos em eventos
    idx = np.where(bursty)[0]
    eventos = []
    if len(idx):
        grupos = np.split(idx, np.where(np.diff(idx) > 1)[0] + 1)
        for grp in grupos:
            sub = P[:, grp]
            linha_tempo = sub.max(axis=1)                # potência do evento ao longo do tempo
            base_evt = float(np.median(baseline_f[grp]))
            on = linha_tempo > (base_evt + MARGEM_DB)
            n_on = int(on.sum())
            if n_on < MIN_ON:
                continue
            # contiguidade: maior bloco contínuo de ON (rajada = bloco; FM = espalhado)
            d = np.diff(np.concatenate(([0], on.astype(int), [0])))
            ini_runs = np.where(d == 1)[0]
            fim_runs = np.where(d == -1)[0]
            runs = (fim_runs - ini_runs) if len(ini_runs) else np.array([0])
            longest = int(runs.max())
            if longest / max(n_on, 1) < CONTIG_MIN:
                continue          # atividade espalhada no tempo = não é rajada (ex.: FM)
            t_ini = int(np.argmax(on))
            pico_bin = grp[int(np.argmax(pico_f[grp]))]
            off_hz = (pico_bin - NFFT / 2) * (sr / NFFT)
            f_hz = freq_hz + off_hz
            cat, cor, desc = _classificar(f_hz)
            eventos.append({
                "freq_mhz": round(f_hz / 1e6, 3),
                "off_khz": round(off_hz / 1e3, 1),
                "pico_db": round(float(pico_f[grp].max()), 1),
                "bw_khz": round(float(len(grp) * sr / NFFT / 1e3), 1),
                "dur_ms": round(longest * dt * 1000, 1),
                "duty": round(float(n_on / nt), 3),
                "t_ini_ms": round(t_ini * dt * 1000, 1),
                "cat": cat, "cor": cor, "desc": desc,
            })
    eventos.sort(key=lambda e: -e["pico_db"])

    # waterfall reduzido (tempo × freq) p/ o canvas: 256x256 grayscale
    Wt, Wf = min(256, nt), min(256, NFFT)
    ti = np.linspace(0, nt - 1, Wt).astype(int)
    fi = np.linspace(0, NFFT - 1, Wf).astype(int)
    sub = P[np.ix_(ti, fi)]
    lo, hi = np.percentile(sub, 5), np.percentile(sub, 99)
    g8 = np.clip((sub - lo) / (hi - lo + 1e-9), 0, 1)
    gray = (g8 * 255).astype(np.uint8)

    return {
        "ok": True,
        "freq_mhz": freq_mhz,
        "sr": sr,
        "span_mhz": round(sr / 1e6, 1),
        "dur_s": dur_s,
        "n_eventos": len(eventos),
        "eventos": eventos,
        "waterfall": {"w": int(Wf), "h": int(Wt), "b64": _b64(gray.tobytes())},
    }
