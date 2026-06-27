#!/usr/bin/env python3
"""
mtzRF — Decode de vídeo analógico (TSCM Fase 2)
==================================================
Tenta reconstruir a imagem de uma câmera analógica sem fio (FPV/espiã) que
transmite vídeo composto (CVBS, NTSC ou PAL) por FM nas bandas de 1.2 / 2.4 / 5.8 GHz.

Pipeline:
  1. captura IQ de banda larga (hackrf_transfer -n para arquivo temporário)
  2. demodula FM (discriminador de fase) → sinal de vídeo composto (baseband)
  3. detecta os pulsos de sincronismo horizontal e fatia em linhas
  4. empilha as linhas em um frame em tons de cinza (luma)

⚠ Experimental: só funciona com câmeras ANALÓGICAS FM. Câmeras digitais/WiFi são
criptografadas e não podem ser decodificadas — aqui só aparecem como ruído.
Câmeras analógicas são cada vez mais raras, então o normal é não haver imagem.
"""

import os
import tempfile
import subprocess

import numpy as np

import hackrf_resource

# Padrões de TV analógica (linhas/seg, linhas ativas exibidas)
PADROES = {
    "NTSC": {"fline": 15734.264, "ativas": 240, "fps": 30},
    "PAL":  {"fline": 15625.0,   "ativas": 288, "fps": 25},
}

# Geometria do sinal de linha (CVBS, microssegundos)
_SYNC_US   = 4.7      # largura do pulso de sync horizontal
_BACK_US   = 9.4      # do início do sync até o vídeo ativo (sync + back porch)
_ACTIVE_US = 51.5     # janela de vídeo ativo por linha


def _capturar_iq(freq_mhz: float, sr: int, dur_s: float,
                 lna: int, vga: int, amp: bool) -> np.ndarray | None:
    """Captura um burst de IQ via hackrf_transfer (-n) e devolve complex64."""
    n_amostras = int(sr * dur_s)
    tmp = tempfile.NamedTemporaryFile(suffix=".iq", delete=False)
    tmp.close()
    cmd = ["hackrf_transfer", "-r", tmp.name,
           "-f", str(int(freq_mhz * 1e6)),
           "-s", str(int(sr)),
           "-g", str(vga), "-l", str(lna),
           "-n", str(n_amostras)]
    if amp:
        cmd += ["-a", "1"]
    try:
        if not hackrf_resource.acquire("tscm-video", timeout=8.0):
            return None
        try:
            subprocess.run(cmd, capture_output=True,
                           timeout=max(8, dur_s * 6 + 6))
        finally:
            hackrf_resource.release()
        raw = np.fromfile(tmp.name, dtype=np.int8)
    except (OSError, subprocess.TimeoutExpired):
        return None
    finally:
        try: os.unlink(tmp.name)
        except OSError: pass

    if raw.size < 1000:
        return None
    raw = raw[: (raw.size // 2) * 2].astype(np.float32)
    iq = raw[0::2] + 1j * raw[1::2]
    return iq.astype(np.complex64)


def _demod_fm(iq: np.ndarray) -> np.ndarray:
    """Discriminador FM → vídeo composto (baseband)."""
    if iq.size < 4:
        return np.zeros(0, dtype=np.float32)
    d = np.angle(iq[1:] * np.conj(iq[:-1])).astype(np.float32)
    return d


def _normalizar(v: np.ndarray) -> np.ndarray:
    """Mapeia para 0..1 com clipping robusto por percentis."""
    if v.size == 0:
        return v
    lo, hi = np.percentile(v, 1), np.percentile(v, 99)
    if hi - lo < 1e-9:
        return np.zeros_like(v)
    return np.clip((v - lo) / (hi - lo), 0.0, 1.0)


def _decodificar(video: np.ndarray, sr: int, padrao: str,
                 largura: int = 360) -> dict:
    """Fatia o vídeo composto em linhas usando o sync horizontal e monta um frame."""
    cfg = PADROES.get(padrao, PADROES["NTSC"])
    spl = sr / cfg["fline"]                 # amostras por linha (float)
    H   = cfg["ativas"]

    v = _normalizar(video)
    if v.size < int(spl * (H + 20)):
        return {"ok": False, "motivo": "captura curta demais para um frame"}

    # Suaviza para tolerar o ruído do link FM (janela ~1µs, < largura do sync 4.7µs)
    win = max(2, int(1.0e-6 * sr))
    vs = np.convolve(v, np.ones(win) / win, mode="same").astype(np.float32)

    # Detecta sync como TRECHOS contínuos abaixo do limiar com duração mínima.
    # Isso rejeita os micro-cruzamentos causados por ruído.
    min_sync = int(2.5e-6 * sr)      # sync dura ~4.7µs; exige ao menos 2.5µs
    def sync_starts(sig):
        thr = np.percentile(sig, 15)
        below = (sig < thr).astype(np.int8)
        d = np.diff(below)
        ini = np.where(d == 1)[0] + 1
        fim = np.where(d == -1)[0] + 1
        if below[0]:  ini = np.concatenate(([0], ini))
        if below[-1]: fim = np.concatenate((fim, [below.size]))
        m = min(len(ini), len(fim))
        ini, fim = ini[:m], fim[:m]
        dur = fim - ini
        return ini[dur >= min_sync]

    starts_lo = sync_starts(vs)        # sync no mínimo
    starts_hi = sync_starts(1.0 - vs)  # polaridade invertida (sync no máximo)

    def regularidade(starts):
        if len(starts) < 20:
            return 0.0
        difs = np.diff(starts)
        return float(np.sum(np.abs(difs - spl) < spl * 0.15) / len(difs))

    r_lo = regularidade(starts_lo)
    r_hi = regularidade(starts_hi)
    if r_hi > r_lo:
        v = 1.0 - v
        starts = starts_hi
        qualidade = r_hi
    else:
        starts = starts_lo
        qualidade = r_lo

    if qualidade < 0.25:
        return {"ok": False, "motivo": "sem sincronismo de vídeo analógico detectável",
                "qualidade": round(float(qualidade), 2)}

    # Mantém apenas starts espaçados ~1 linha (filtra equalizing/ruído residual)
    linhas = []
    last = -spl
    for s in starts:
        if s - last >= spl * 0.6:
            linhas.append(int(s))
            last = s

    # Geometria da linha em amostras
    a0 = int(_BACK_US   * 1e-6 * sr)        # início do vídeo ativo após o sync
    aw = int(_ACTIVE_US * 1e-6 * sr)        # largura do vídeo ativo

    linhas = np.array(linhas)
    # Escolhe o trecho com linhas mais bem espaçadas (~spl) para evitar tearing:
    # a maior sequência contígua de espaçamentos dentro da tolerância.
    if len(linhas) > H:
        difs = np.diff(linhas)
        bom = np.abs(difs - spl) < spl * 0.10
        melhor_ini, melhor_len, cur_ini, cur_len = 0, 0, 0, 0
        for k, ok_ in enumerate(bom):
            if ok_:
                if cur_len == 0:
                    cur_ini = k
                cur_len += 1
                if cur_len > melhor_len:
                    melhor_len, melhor_ini = cur_len, cur_ini
            else:
                cur_len = 0
        linhas = linhas[melhor_ini: melhor_ini + melhor_len + 1]

    rows = []
    xs_src = np.linspace(0, aw - 1, largura)
    for s in linhas:
        ini = int(s) + a0
        fim = ini + aw
        if fim >= v.size:
            break
        seg = v[ini:fim]
        # reamostra a janela ativa para 'largura' colunas
        row = np.interp(xs_src, np.arange(seg.size), seg)
        rows.append(row)

    if len(rows) < int(H * 0.6):
        return {"ok": False, "motivo": f"poucas linhas decodificadas ({len(rows)})",
                "qualidade": round(float(qualidade), 2)}

    melhor = np.array(rows[:H])
    # contraste final
    g = _normalizar(melhor.flatten()).reshape(melhor.shape)
    gray = (g * 255).astype(np.uint8)

    return {
        "ok": True,
        "w": int(gray.shape[1]),
        "h": int(gray.shape[0]),
        "padrao": padrao,
        "qualidade": round(float(qualidade), 2),
        "linhas": len(rows),
        "gray_b64": _b64(gray.tobytes()),
    }


def _b64(b: bytes) -> str:
    import base64
    return base64.b64encode(b).decode("ascii")


def analisar(freq_mhz: float, padrao: str = "auto", sr: int = 16_000_000,
             dur_s: float = 0.20, lna: int = 24, vga: int = 32,
             amp: bool = False) -> dict:
    """Captura e tenta decodificar vídeo analógico na frequência dada."""
    iq = _capturar_iq(freq_mhz, sr, dur_s, lna, vga, amp)
    if iq is None:
        return {"ok": False, "motivo": "falha na captura de IQ (HackRF ocupado ou erro)"}

    video = _demod_fm(iq)
    if video.size == 0:
        return {"ok": False, "motivo": "demodulação vazia"}

    padroes = ["NTSC", "PAL"] if padrao == "auto" else [padrao.upper()]
    melhor = {"ok": False, "motivo": "sem vídeo", "qualidade": -1}
    for p in padroes:
        r = _decodificar(video, sr, p)
        if r.get("ok") and r.get("qualidade", 0) > melhor.get("qualidade", -1):
            melhor = r
        elif not melhor.get("ok") and r.get("qualidade", -1) > melhor.get("qualidade", -1):
            melhor = r

    melhor["freq_mhz"] = freq_mhz
    melhor["sr"] = sr
    return melhor
