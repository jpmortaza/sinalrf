#!/usr/bin/env python3
"""
mtzRF — TSCM Scanner (contra-vigilância)
==========================================
Varre faixas usadas por escutas (microfones sem fio, transmissores FM/NFM) e
câmeras espiãs (1.2 / 2.4 / 5.8 GHz), detecta transmissões ativas, estima a
largura de banda e classifica cada sinal como provável escuta de áudio, câmera,
WiFi, celular ou broadcast conhecido.

Não abre o HackRF diretamente além do hackrf_sweep — o acesso é serializado por
hackrf_resource (o mesmo lock usado pelo rádio/IMSI).

A "escuta" de um sinal de áudio é feita reaproveitando o endpoint /ws/radio.
"""

import subprocess
import numpy as np

import hackrf_resource

# ── Presets de banda (TSCM) ──────────────────────────────────────────────────────
# Cada preset: faixas (MHz) a varrer + resolução de bin (Hz).
BANDAS = {
    "audio": {
        "label": "Escutas áudio (VHF/UHF)",
        "ranges": [(80, 470)],
        "bin_hz": 100_000,
    },
    "cam24": {
        "label": "Câmeras 2.4 GHz",
        "ranges": [(2400, 2500)],
        "bin_hz": 500_000,
    },
    "cam1258": {
        "label": "Câmeras 1.2 / 5.8 GHz",
        "ranges": [(1100, 1300), (5650, 5950)],
        "bin_hz": 1_000_000,
    },
    "gsm": {
        "label": "GSM/Celular (escutas com SIM)",
        "ranges": [(870, 960), (1710, 1880)],
        "bin_hz": 200_000,
    },
    "full": {
        "label": "Varredura completa (80 MHz–6 GHz)",
        "ranges": [(80, 6000)],
        "bin_hz": 1_000_000,
    },
}

# Baselines por banda (centros conhecidos) — para marcar sinais NOVOS.
_baselines: dict[str, list[float]] = {}


# ── Sweep ────────────────────────────────────────────────────────────────────────
def _varrer_range(f_min: int, f_max: int, bin_hz: int,
                  lna: int, vga: int, amp: bool, timeout: int) -> list[tuple[float, float]]:
    """Roda hackrf_sweep numa faixa e retorna [(freq_mhz, dbm), ...]."""
    cmd = ["hackrf_sweep",
           "-f", f"{int(f_min)}:{int(f_max)}",
           "-l", str(lna),
           "-g", str(vga),
           "-w", str(int(bin_hz)),
           "-1",            # uma varredura
           "-r", "-"]
    if amp:
        cmd += ["-a", "1"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return []

    pontos: list[tuple[float, float]] = []
    for linha in proc.stdout.strip().split("\n"):
        partes = linha.split(",")
        if len(partes) < 7:
            continue
        try:
            hz_low = float(partes[2].strip())
            hz_bin = float(partes[4].strip())
            for i, x in enumerate(partes[6:]):
                x = x.strip()
                if x in ("", "nan", "-inf", "inf"):
                    continue
                db = float(x)
                freq_mhz = (hz_low + (i + 0.5) * hz_bin) / 1e6
                if f_min <= freq_mhz <= f_max:
                    pontos.append((freq_mhz, db))
        except (ValueError, IndexError):
            continue
    return sorted(pontos, key=lambda p: p[0])


# ── Detecção de sinais ────────────────────────────────────────────────────────────
def _detectar(pontos: list[tuple[float, float]], bin_hz: int,
              margem_db: float = 10.0) -> tuple[list[dict], float]:
    """Agrupa bins contíguos acima do limiar em sinais. Retorna (sinais, piso_db)."""
    if len(pontos) < 8:
        return [], -120.0

    freqs = np.array([p[0] for p in pontos])
    dbs   = np.array([p[1] for p in pontos])

    piso = float(np.percentile(dbs, 40))          # ruído de fundo
    limiar = piso + margem_db
    gap_max = (bin_hz / 1e6) * 2.5                 # distância p/ considerar bins vizinhos
    HOLES_MAX = 2                                  # nº de bins abaixo do limiar tolerados dentro de um sinal

    sinais = []
    i = 0
    n = len(freqs)
    while i < n:
        if dbs[i] < limiar:
            i += 1
            continue
        # estende o cluster enquanto houver energia, tolerando poucos buracos
        j = i
        holes = 0
        while j + 1 < n:
            adjacente = (freqs[j + 1] - freqs[j]) <= gap_max
            if dbs[j + 1] >= limiar and adjacente:
                j += 1
                holes = 0
            elif adjacente and holes < HOLES_MAX:
                j += 1
                holes += 1
            else:
                break

        seg_f = freqs[i:j + 1]
        seg_d = dbs[i:j + 1]
        # apara caudas abaixo do limiar nas bordas (sobra dos buracos)
        acima = np.where(seg_d >= limiar)[0]
        if len(acima) == 0:
            i = j + 1
            continue
        seg_f = seg_f[acima[0]:acima[-1] + 1]
        seg_d = seg_d[acima[0]:acima[-1] + 1]

        pot = np.power(10.0, seg_d / 10.0)
        centro = float(np.sum(seg_f * pot) / np.sum(pot))
        pico = float(np.max(seg_d))
        bw_khz = float((seg_f[-1] - seg_f[0]) * 1000.0 + bin_hz / 1000.0)
        snr = round(pico - piso, 1)

        sinais.append({
            "freq_mhz": round(centro, 3),
            "dbm":      round(pico, 1),
            "bw_khz":   round(bw_khz, 1),
            "snr_db":   snr,
            "f_ini":    round(float(seg_f[0]), 3),
            "f_fim":    round(float(seg_f[-1]), 3),
        })
        i = j + 1

    sinais.sort(key=lambda s: -s["dbm"])
    return sinais[:80], round(piso, 1)


# ── Classificação TSCM ─────────────────────────────────────────────────────────────
def _classificar(s: dict) -> dict:
    """Adiciona cat, modo de demodulação, nível de suspeita e descrição."""
    f = s["freq_mhz"]
    bw = s["bw_khz"]

    cat, desc, modo, suspeita = "DESCONHECIDO", "Sinal não identificado", "NFM", 1

    if 88 <= f <= 108:
        if bw <= 130:
            cat, desc, modo, suspeita = "ESCUTA?", "FM estreito na banda comercial — possível microfone espião", "WFM", 3
        else:
            cat, desc, modo, suspeita = "FM-BCAST", "Rádio FM comercial", "WFM", 0
    elif 108 <= f <= 137:
        cat, desc, modo, suspeita = "AERONAV", "Aviação (AM)", "AM", 0
    elif (138 <= f <= 174) or (400 <= f <= 470):
        if bw <= 60:
            cat, desc, modo, suspeita = "ESCUTA?", "VHF/UHF estreito — típico de escuta/walkie", "NFM", 3
        else:
            cat, desc, modo, suspeita = "VHF-UHF", "Rádio profissional/serviço", "NFM", 1
    elif 174 <= f <= 230:
        cat, desc, modo, suspeita = "DAB/TV", "TV/DAB digital", None, 0
    elif 470 <= f <= 700:
        cat, desc, modo, suspeita = "TV-UHF", "TV digital UHF", None, 0
    elif (870 <= f <= 960) or (1710 <= f <= 1880) or (1920 <= f <= 2170):
        cat, desc, modo, suspeita = "CELULAR", "Celular GSM/LTE — escuta com SIM transmite aqui", None, 2
    elif 1100 <= f <= 1300:
        if bw >= 3000:
            cat, desc, modo, suspeita = "CAM-VID", "Banda larga em 1.2 GHz — possível câmera analógica", None, 3
        else:
            cat, desc, modo, suspeita = "1.2GHz", "Sinal em 1.2 GHz", None, 2
    elif 2400 <= f <= 2500:
        if bw >= 10000:
            cat, desc, modo, suspeita = "WiFi/CAM", "Banda larga 2.4 GHz — WiFi ou câmera sem fio", None, 2
        elif bw >= 3000:
            cat, desc, modo, suspeita = "CAM-VID", "2.4 GHz banda média — possível câmera analógica", None, 3
        else:
            cat, desc, modo, suspeita = "ISM-2.4", "Dispositivo ISM 2.4 GHz (BT/controle)", None, 2
    elif 5650 <= f <= 5950:
        if bw >= 6000:
            cat, desc, modo, suspeita = "CAM-VID", "Banda larga 5.8 GHz — câmera FPV/analógica ou WiFi 5G", None, 3
        else:
            cat, desc, modo, suspeita = "5.8GHz", "Sinal em 5.8 GHz", None, 2

    out = dict(s)
    out.update({"cat": cat, "desc": desc, "modo": modo, "suspeita": suspeita})
    return out


# ── API principal ──────────────────────────────────────────────────────────────────
def escanear(banda: str = "audio", lna: int = 32, vga: int = 40,
             amp: bool = False) -> dict:
    """Varre uma banda preset e devolve sinais classificados."""
    preset = BANDAS.get(banda, BANDAS["audio"])
    bin_hz = preset["bin_hz"]

    if not hackrf_resource.acquire(f"tscm-{banda}", timeout=10.0):
        return {"ok": False, "erro": "HackRF ocupado — pare a escuta antes de varrer", "banda": banda}

    todos: list[tuple[float, float]] = []
    try:
        for (f0, f1) in preset["ranges"]:
            span = f1 - f0
            tout = 12 if span <= 200 else (30 if span <= 600 else 60)
            todos += _varrer_range(f0, f1, bin_hz, lna, vga, amp, tout)
    finally:
        hackrf_resource.release()

    if not todos:
        return {"ok": False, "erro": "Sem dados do HackRF (sweep vazio)", "banda": banda}

    sinais_raw, piso = _detectar(todos, bin_hz)
    sinais = [_classificar(s) for s in sinais_raw]

    # marca novos vs baseline
    base = _baselines.get(banda)
    if base:
        for s in sinais:
            tol = max(0.3, s["bw_khz"] / 1000.0)
            s["novo"] = not any(abs(s["freq_mhz"] - b) <= tol for b in base)
    else:
        for s in sinais:
            s["novo"] = False

    # ordena: suspeita desc, depois potência
    sinais.sort(key=lambda s: (-s["suspeita"], -s["dbm"]))

    return {
        "ok": True,
        "banda": banda,
        "label": preset["label"],
        "piso_db": piso,
        "n_sinais": len(sinais),
        "tem_baseline": bool(base),
        "sinais": sinais,
        # panorama leve p/ desenhar mini-espectro no cliente
        "panorama": {
            "freqs": [round(f, 3) for f, _ in todos[::max(1, len(todos) // 1200)]],
            "dbm":   [round(d, 1) for _, d in todos[::max(1, len(todos) // 1200)]],
        },
    }


def salvar_baseline(banda: str, sinais: list) -> int:
    """Guarda os centros atuais como baseline da banda. Retorna a contagem."""
    centros = [float(s["freq_mhz"]) for s in sinais if "freq_mhz" in s]
    _baselines[banda] = centros
    return len(centros)


def limpar_baseline(banda: str):
    _baselines.pop(banda, None)
