#!/usr/bin/env python3
"""
mtzRF — Agendador e Alertas
===========================
Roda varreduras em background num intervalo e gera ALERTAS ao detectar mudanças:
  • WiFi  — novo rogue AP / evil-twin, novo AP forte
  • Rede  — nova câmera IP, novo dispositivo

Usa só WiFi (netsh) e rede (ping/socket) — NÃO usa o HackRF, então roda em paralelo
sem conflitar com o scanner/rádio. (Monitoramento TSCM por HackRF fica p/ depois.)
"""

import time
import threading

import wifi_tools
import net_scanner

_cfg = {"ativo": False, "intervalo_s": 300, "tarefas": ["wifi", "rede"]}
_estado = {"ultima_exec": None, "rodando": False}
_alertas: list = []
_prev = {"wifi_rogue": set(), "wifi_bssid": set(), "rede_host": set()}
_lock = threading.Lock()
_MAX = 200
_seq = 0


def _add(tipo: str, nivel: int, msg: str):
    global _seq
    with _lock:
        _seq += 1
        _alertas.insert(0, {
            "id": _seq,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "tipo": tipo, "nivel": nivel, "msg": msg,
        })
        del _alertas[_MAX:]


def _ciclo_wifi():
    redes = wifi_tools.escanear_redes()
    if not redes:
        return
    for a in wifi_tools.detectar_rogue(redes):
        chave = a["ssid"] + "|" + a["motivo"]
        if chave not in _prev["wifi_rogue"]:
            _prev["wifi_rogue"].add(chave)
            _add("wifi", min(3, a.get("nivel", 2)), f"Rogue AP: {a['ssid']} — {a['motivo']}")
    cur = {r["bssid"] for r in redes}
    if _prev["wifi_bssid"]:
        novos = cur - _prev["wifi_bssid"]
        for r in [x for x in redes if x["bssid"] in novos and (x.get("sinal") or 0) >= 65][:5]:
            _add("wifi", 1, f"Novo AP forte: {r['ssid']} ({r['bssid']}) {r.get('sinal')}%")
    _prev["wifi_bssid"] = cur


def _ciclo_rede():
    res = net_scanner.escanear()
    if not res.get("ok"):
        return
    devs = res.get("dispositivos") or []
    cur = {(d.get("mac") or d.get("ip")) for d in devs}
    if _prev["rede_host"]:
        for d in devs:
            k = d.get("mac") or d.get("ip")
            if k and k not in _prev["rede_host"]:
                if d.get("camera"):
                    _add("rede", 2, f"Nova CÂMERA na rede: {d['ip']} {d.get('vendor','')}".strip())
                else:
                    _add("rede", 1, f"Novo dispositivo: {d['ip']} {d.get('hostname','') or d.get('mac','')}".strip())
    _prev["rede_host"] = cur


def _loop():
    while True:
        if not _cfg["ativo"]:
            time.sleep(2)
            continue
        _estado["rodando"] = True
        try:
            if "wifi" in _cfg["tarefas"]:
                _ciclo_wifi()
            if "rede" in _cfg["tarefas"]:
                _ciclo_rede()
        except Exception:
            pass
        _estado["rodando"] = False
        _estado["ultima_exec"] = time.strftime("%Y-%m-%d %H:%M:%S")
        for _ in range(max(30, _cfg["intervalo_s"])):
            if not _cfg["ativo"]:
                break
            time.sleep(1)


def iniciar():
    threading.Thread(target=_loop, daemon=True, name="agendador").start()


def configurar(ativo=None, intervalo_s=None, tarefas=None) -> dict:
    if ativo is not None:
        _cfg["ativo"] = bool(ativo)
        if _cfg["ativo"]:
            # nova ativação: zera baseline p/ não disparar tudo de uma vez
            _prev["wifi_bssid"] = set(); _prev["rede_host"] = set(); _prev["wifi_rogue"] = set()
    if intervalo_s is not None:
        _cfg["intervalo_s"] = max(30, int(intervalo_s))
    if tarefas is not None:
        _cfg["tarefas"] = [t for t in tarefas if t in ("wifi", "rede")]
    return estado()


def estado() -> dict:
    return {"cfg": dict(_cfg), "ultima_exec": _estado["ultima_exec"],
            "rodando": _estado["rodando"], "n_alertas": len(_alertas)}


def alertas() -> list:
    with _lock:
        return list(_alertas)


def limpar():
    with _lock:
        _alertas.clear()
