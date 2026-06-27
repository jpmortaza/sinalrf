#!/usr/bin/env python3
"""
mtzRF — Decoder ISM 433/868 MHz (estilo rtl_433)
=================================================
Captura IQ numa banda ISM, detecta transmissões OOK/ASK (a modulação da maioria
dos sensores domésticos, controles, campainhas, estações meteo e TPMS de pneus),
mede as larguras de pulso e decodifica os bits/hex de cada pacote.

Não identifica o modelo exato (isso é trabalho do rtl_433, com centenas de decoders),
mas mostra PRESENÇA + payload bruto + timing — suficiente para auditoria de privacidade
física (ex.: "um sensor/TPMS transmitiu aqui") e fingerprint. Só recepção.
"""

import os
import tempfile
import subprocess

import numpy as np

import hackrf_resource

SR = 2_000_000


def _capturar(freq_hz: int, dur_s: float, lna: int, vga: int, amp: bool):
    n = int(SR * dur_s)
    tmp = tempfile.NamedTemporaryFile(suffix=".iq", delete=False)
    tmp.close()
    cmd = ["hackrf_transfer", "-r", tmp.name, "-f", str(freq_hz), "-s", str(SR),
           "-g", str(vga), "-l", str(lna), "-n", str(n)]
    if amp:
        cmd += ["-a", "1"]
    try:
        if not hackrf_resource.acquire("ism", timeout=6.0):
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
    if raw.size < 4000:
        return None
    raw = raw[:(raw.size // 2) * 2].astype(np.float32)
    return np.abs(raw[0::2]) + np.abs(raw[1::2])      # envelope (|I|+|Q|)


def _runs(mask: np.ndarray):
    """Retorna (inícios, comprimentos, valor) dos runs de uma máscara booleana."""
    if mask.size == 0:
        return np.array([]), np.array([]), np.array([])
    d = np.diff(mask.astype(np.int8))
    bordas = np.where(d != 0)[0] + 1
    ini = np.concatenate(([0], bordas))
    fim = np.concatenate((bordas, [mask.size]))
    comp = fim - ini
    val = mask[ini]
    return ini, comp, val


MIN_PULSO_US = 30.0       # pulsos < 30µs = ruído
MAX_PULSO_US = 5000.0     # pulsos > 5ms não são OOK típico
MAX_PULSOS = 300          # pacote OOK real raramente passa disso


def _decodificar_pacote(larguras_us: np.ndarray):
    """Tenta PWM por largura de pulso: 2 clusters (curto=0, longo=1) -> bits/hex."""
    if not (8 <= len(larguras_us) <= MAX_PULSOS):
        return None
    med = float(np.median(larguras_us))
    if med < MIN_PULSO_US or med > MAX_PULSO_US:   # timing fora do OOK plausível
        return None
    lo, hi = np.percentile(larguras_us, 20), np.percentile(larguras_us, 80)
    if hi < lo * 1.4:        # sem dois níveis claros -> não é PWM por pulso
        return None
    thr = (lo + hi) / 2
    bits = (larguras_us > thr).astype(np.uint8)
    nbits = len(bits)
    pad = (-nbits) % 8
    by = np.packbits(np.concatenate([bits, np.zeros(pad, dtype=np.uint8)]))
    return {
        "bits": int(nbits),
        "hex": by.tobytes().hex().upper(),
        "curto_us": round(float(lo), 1),
        "longo_us": round(float(hi), 1),
    }


def escanear(freq_mhz: float = 433.92, dur_s: float = 3.0,
             lna: int = 32, vga: int = 40, amp: bool = True) -> dict:
    env = _capturar(int(freq_mhz * 1e6), dur_s, lna, vga, amp)
    if env is None:
        return {"ok": False, "motivo": "falha na captura (HackRF ocupado ou erro)"}

    # suaviza p/ reduzir ruído (janela ~2µs) e define limiar OOK
    w = 4
    env = np.convolve(env, np.ones(w) / w, mode="same")
    piso = float(np.percentile(env, 50))
    teto = float(np.percentile(env, 99.5))
    if teto < piso + 4:
        return {"ok": True, "freq_mhz": freq_mhz, "n_pacotes": 0, "pacotes": [],
                "motivo": "sem transmissões OOK acima do ruído"}
    thr = piso + (teto - piso) * 0.4
    on = env > thr

    ini, comp, val = _runs(on)
    us = 1e6 / SR
    # pulsos = runs ON ; gaps = runs OFF
    pulso_idx = np.where(val)[0]
    if pulso_idx.size < 6:
        return {"ok": True, "freq_mhz": freq_mhz, "n_pacotes": 0, "pacotes": []}

    # separa em pacotes por GAP longo (fim de pacote)
    gap_us = comp * us
    gaps_off = gap_us[~val.astype(bool)] if (~val).any() else np.array([1000.0])
    gap_corte = max(2000.0, float(np.percentile(gaps_off, 95)) * 2)   # µs

    min_pulso = MIN_PULSO_US / us        # em amostras
    pacotes = []
    cur = []
    for k in range(len(comp)):
        if val[k]:                       # run ON
            if comp[k] >= min_pulso:     # pulso real (ignora ruído curto)
                cur.append(comp[k] * us)
                if len(cur) > MAX_PULSOS + 5:   # blob de ruído -> descarta
                    cur = []
        else:                            # gap OFF
            if comp[k] * us > gap_corte and cur:
                pacotes.append(cur); cur = []
    if cur:
        pacotes.append(cur)
    pacotes = [p for p in pacotes if 8 <= len(p) <= MAX_PULSOS]

    # decodifica cada pacote e deduplica repetições idênticas
    achados = {}
    for p in pacotes:
        if len(p) < 8:
            continue
        dec = _decodificar_pacote(np.array(p))
        if not dec:
            continue
        chave = dec["hex"]
        if chave in achados:
            achados[chave]["rep"] += 1
        else:
            achados[chave] = {
                "hex": dec["hex"], "bits": dec["bits"], "n_pulsos": len(p),
                "dur_ms": round(sum(p) / 1000, 1),
                "curto_us": dec["curto_us"], "longo_us": dec["longo_us"], "rep": 1,
            }
    lista = sorted(achados.values(), key=lambda a: -a["rep"])

    return {
        "ok": True, "freq_mhz": freq_mhz, "sr": SR, "dur_s": dur_s,
        "piso_env": round(piso, 1), "n_pacotes": len(lista), "pacotes": lista,
    }
