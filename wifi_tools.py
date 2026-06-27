#!/usr/bin/env python3
"""
mtzRF — WiFi Red Team (testes autorizados)
=============================================
Ferramentas de teste de segurança WiFi para engajamentos AUTORIZADOS:

  • Gerenciador de adaptadores  — lista/seleciona as placas WiFi
  • Recon WiFi                  — escaneia APs vizinhos (BSSID, canal, segurança, sinal)
  • Detecção de rogue AP        — flagra evil-twin / APs falsos
  • Portal cativo (awareness)   — captura local + revelação educativa (anti-phishing)

⚠ Uso restrito a redes/dispositivos próprios ou com autorização escrita.
NÃO inclui deauth/jamming (negação de serviço). No Windows não há modo monitor /
múltiplos APs — evil-twin completo exige Linux/hostapd ou ESP32.
"""

import os
import re
import json
import time
import unicodedata
import subprocess
from pathlib import Path

_DIR = Path(__file__).parent
CAPTURAS_LOG = _DIR / "capturas_portal.jsonl"

# Estado da campanha de portal (salvaguarda: só captura se 'armado' com autorização)
_portal = {"armado": False, "campanha": "", "autorizado": False, "ts": 0.0}


# ── util ──────────────────────────────────────────────────────────────────────────
def _sa(s: str) -> str:
    """strip-accents + lower, para casar rótulos localizados do netsh."""
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn").lower()


def _run(cmd: list, timeout: int = 12) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           errors="ignore", timeout=timeout)
        return r.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


def checar_acesso() -> dict:
    """Detecta a trava do Windows 11 (Localização/elevação) que impede ler WiFi."""
    out = _run(["netsh", "wlan", "show", "interfaces"])
    o = _sa(out)
    if any(k in o for k in ("permissao de local", "location permission",
                            "elevacao", "elevation", "erro 5", "error 5",
                            "servicos de localizacao", "location service")):
        return {"ok": False, "motivo": "loc",
                "msg": "O Windows 11 exige Serviços de Localização ATIVADOS (e às vezes executar como admin) "
                       "para ler o WiFi. Ative em Configurações ▸ Privacidade e segurança ▸ Localização "
                       "(e 'Permitir que apps da área de trabalho acessem sua localização')."}
    if not out.strip():
        return {"ok": False, "motivo": "wlan",
                "msg": "Serviço WLAN AutoConfig indisponível ou nenhuma placa WiFi ativa."}
    return {"ok": True, "motivo": "", "msg": ""}


def _grab(bloco: str, chaves: list) -> str:
    """1º valor cujo rótulo (sem acento) contém uma das chaves."""
    for linha in bloco.splitlines():
        if ":" in linha:
            rot, _, val = linha.partition(":")
            r = _sa(rot)
            if any(k in r for k in chaves):
                return val.strip()
    return ""


# ── Adaptadores ─────────────────────────────────────────────────────────────────────
def listar_adaptadores() -> list[dict]:
    """Lista as interfaces WiFi e capacidades (rede hospedada/AP)."""
    saida = _run(["netsh", "wlan", "show", "interfaces"])
    drivers = _run(["netsh", "wlan", "show", "drivers"])

    # capacidades por nome de interface (rede hospedada compatível)
    cap = {}
    for bloco in re.split(r"\n\s*\n", drivers):
        nome = _grab(bloco, ["nome da interface", "interface name"])
        if nome:
            cap[nome.strip()] = {
                "ap_suportado": _sa(_grab(bloco, ["rede hospedada", "hosted network"])).startswith(("s", "y")),
                "radios": _grab(bloco, ["tipos de radio", "radio types supported"]),
            }

    adapters = []
    blocos = re.split(r"\n\s*\n", saida)
    for b in blocos:
        nome = _grab(b, ["nome", "name"])
        if not nome or not _grab(b, ["descricao", "description"]):
            continue
        c = cap.get(nome.strip(), {})
        adapters.append({
            "nome": nome.strip(),
            "descricao": _grab(b, ["descricao", "description"]),
            "estado": _grab(b, ["estado", "state"]),
            "ssid": _grab(b, ["ssid"]) if "ssid" in _sa(b) else "",
            "mac": _grab(b, ["endereco fisico", "physical address", "bssid"]),
            "canal": _grab(b, ["canal", "channel"]),
            "radio": _grab(b, ["tipo de radio", "radio type"]),
            "ap_suportado": c.get("ap_suportado", False),
            "radios": c.get("radios", ""),
        })
    return adapters


# ── Recon WiFi ───────────────────────────────────────────────────────────────────────
def _norm_seg(auth: str) -> str:
    a = _sa(auth)
    if not a or "abert" in a or "open" in a or a == "-":
        return "ABERTA"
    if "wpa3" in a: return "WPA3"
    if "wpa2" in a: return "WPA2"
    if "wpa"  in a: return "WPA"
    if "wep"  in a: return "WEP"
    return auth.strip() or "?"


def escanear_redes(interface: str | None = None) -> list[dict]:
    """Escaneia APs visíveis (netsh wlan show networks mode=bssid)."""
    cmd = ["netsh", "wlan", "show", "networks", "mode=bssid"]
    if interface:
        cmd.append(f"interface={interface}")
    out = _run(cmd)
    redes = []
    vistos = set()
    blocos = re.split(r"\n(?=SSID \d+\s*:)", out)
    for b in blocos:
        # captura o nome SÓ até o fim da linha (não atravessa \n)
        m = re.search(r"SSID \d+\s*:[ \t]*([^\r\n]*)", b)
        if not m:
            continue
        ssid = m.group(1).strip()
        auth = _grab(b, ["autentica", "authentication"])
        enc  = _grab(b, ["cripto", "encryption"])
        seg  = _norm_seg(auth)
        for bp in re.split(r"\n(?=\s*BSSID \d+\s*:)", b):
            mb = re.search(r"BSSID \d+\s*:\s*([0-9a-fA-F:]{17})", bp)
            if not mb:
                continue
            bssid = mb.group(1).upper()
            if bssid in vistos:
                continue
            vistos.add(bssid)
            sig = re.search(r"(\d{1,3})\s*%", bp)
            canal = _grab(bp, ["canal", "channel"])
            redes.append({
                "ssid": ssid or "(oculto)",
                "bssid": bssid,
                "sinal": int(sig.group(1)) if sig else None,
                "canal": canal,
                "seg": seg,
                "auth": auth,
                "enc": enc,
                "radio": _grab(bp, ["tipo de radio", "radio type"]),
            })
    redes.sort(key=lambda r: -(r["sinal"] or 0))
    return redes


# ── Detecção de rogue AP / evil-twin ───────────────────────────────────────────────
def detectar_rogue(redes: list[dict]) -> list[dict]:
    """Marca sinais suspeitos de evil-twin e devolve a lista de alertas."""
    por_ssid: dict[str, list] = {}
    for r in redes:
        if r["ssid"] and r["ssid"] != "(oculto)":
            por_ssid.setdefault(r["ssid"], []).append(r)

    alertas = []
    for ssid, lst in por_ssid.items():
        bssids = {r["bssid"] for r in lst}
        segs   = {r["seg"] for r in lst}
        ouis   = {r["bssid"][:8] for r in lst}
        if len(bssids) > 1 and len(segs) > 1:
            for r in lst:
                r["rogue"] = True
            alertas.append({
                "ssid": ssid, "nivel": 3,
                "motivo": f"mesmo SSID com segurança diferente ({', '.join(sorted(segs))}) — possível evil-twin",
                "bssids": sorted(bssids),
            })
        elif "ABERTA" in segs and len(bssids) > 1:
            for r in lst:
                if r["seg"] == "ABERTA":
                    r["rogue"] = True
            alertas.append({
                "ssid": ssid, "nivel": 2,
                "motivo": "SSID com versão ABERTA convivendo com versão protegida — clone aberto suspeito",
                "bssids": sorted(bssids),
            })
        elif len(ouis) > 2 and len(bssids) > 3:
            alertas.append({
                "ssid": ssid, "nivel": 1,
                "motivo": f"{len(bssids)} APs com {len(ouis)} fabricantes diferentes (pode ser mesh ou clones)",
                "bssids": sorted(bssids),
            })
    return alertas


# ── Portal cativo (awareness) ──────────────────────────────────────────────────────
def armar_portal(campanha: str, autorizado: bool) -> dict:
    _portal.update({
        "armado": bool(autorizado),
        "campanha": campanha or "sem-nome",
        "autorizado": bool(autorizado),
        "ts": time.time(),
    })
    return estado_portal()


def desarmar_portal() -> dict:
    _portal.update({"armado": False})
    return estado_portal()


def estado_portal() -> dict:
    return {
        "armado": _portal["armado"],
        "campanha": _portal["campanha"],
        "autorizado": _portal["autorizado"],
        "n_capturas": _contar_capturas(),
    }


def registrar_captura(portal: str, campos: dict, ip: str = "") -> dict:
    """Registra uma submissão SÓ se a campanha estiver armada (autorizada)."""
    if not _portal["armado"]:
        return {"ok": True, "logado": False, "motivo": "campanha não armada — nada gravado"}
    item = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "campanha": _portal["campanha"],
        "portal": portal,
        "ip": ip,
        "campos": campos,
        "_aviso": "DADO DE TESTE AUTORIZADO — uso restrito",
    }
    try:
        with open(CAPTURAS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    except OSError:
        return {"ok": False, "logado": False, "motivo": "falha ao gravar"}
    return {"ok": True, "logado": True}


def listar_capturas(limite: int = 200) -> list[dict]:
    if not CAPTURAS_LOG.exists():
        return []
    linhas = CAPTURAS_LOG.read_text(encoding="utf-8", errors="ignore").splitlines()
    out = []
    for ln in linhas[-limite:]:
        try:
            out.append(json.loads(ln))
        except ValueError:
            pass
    return list(reversed(out))


def limpar_capturas():
    try:
        if CAPTURAS_LOG.exists():
            CAPTURAS_LOG.unlink()
    except OSError:
        pass


def _contar_capturas() -> int:
    if not CAPTURAS_LOG.exists():
        return 0
    try:
        return sum(1 for _ in open(CAPTURAS_LOG, encoding="utf-8", errors="ignore"))
    except OSError:
        return 0
