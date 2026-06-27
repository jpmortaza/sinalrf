#!/usr/bin/env python3
"""
mtzRF — Scanner de rede (câmeras WiFi/IP)
============================================
Descobre os dispositivos na MESMA rede que o PC e identifica câmeras IP/WiFi,
mostrando IP, MAC, fabricante, hostname e portas de câmera abertas (RTSP/ONVIF).

Não usa o HackRF — usa a própria conexão de rede do computador (ping/arp + TCP).
Só funciona para câmeras na rede em que você está (é o único jeito de obter o IP;
câmeras em redes de terceiros são criptografadas e não expõem IP).
"""

import re
import socket
import ipaddress
import subprocess
from concurrent.futures import ThreadPoolExecutor

# Portas típicas de câmera IP
CAM_PORTS  = [554, 8554, 80, 8000, 8080, 443, 37777, 34567, 88, 8899, 9000, 10554]
RTSP_PORTS = {554, 8554, 10554}
ONVIF_PORTS = {8000, 8080, 80}

# Prefixos OUI (3 primeiros octetos) de fabricantes comuns de câmera/IoT.
# Best-effort — a detecção forte vem das portas RTSP/ONVIF.
OUI_CAM = {
    "44:19:B6": "Hikvision", "4C:BD:8F": "Hikvision", "28:57:BE": "Hikvision",
    "BC:AD:28": "Hikvision", "C0:56:E3": "Hikvision", "54:C4:15": "Hikvision",
    "3C:EF:8C": "Dahua", "90:02:A9": "Dahua", "14:A7:8B": "Dahua", "E0:50:8B": "Dahua",
    "EC:71:DB": "Reolink", "9C:8E:CD": "Amcrest/Foscam", "00:62:6E": "Foscam",
    "00:40:8C": "Axis", "AC:CC:8E": "Axis", "B8:A4:4F": "Axis",
    "24:5A:4C": "Ubiquiti", "78:8A:20": "Ubiquiti", "FC:EC:DA": "Ubiquiti", "F4:92:BF": "Ubiquiti",
    "2C:AA:8E": "Wyze", "D0:3F:27": "Wyze", "7C:78:B2": "Wyze",
    "64:16:66": "Google Nest", "1C:F2:9A": "Google Nest",
    "00:71:47": "Amazon/Ring", "0C:47:C9": "Amazon/Ring", "44:65:0D": "Amazon/Ring",
    "68:37:E9": "Amazon/Ring", "FC:65:DE": "Amazon/Ring",
    "28:6C:07": "Xiaomi", "50:EC:50": "Xiaomi", "64:09:80": "Xiaomi", "78:11:DC": "Xiaomi",
    "00:31:92": "TP-Link Tapo", "50:C7:BF": "TP-Link", "60:32:B1": "TP-Link", "AC:84:C6": "TP-Link",
    # Espressif/Realtek — muitas câmeras IoT baratas (Tuya, etc.)
    "24:0A:C4": "Espressif (IoT/cam)", "30:AE:A4": "Espressif (IoT/cam)",
    "7C:9E:BD": "Espressif (IoT/cam)", "84:CC:A8": "Espressif (IoT/cam)",
    "A4:CF:12": "Espressif (IoT/cam)", "B4:E6:2D": "Espressif (IoT/cam)",
    "DC:4F:22": "Espressif (IoT/cam)", "EC:FA:BC": "Espressif (IoT/cam)",
    "10:52:1C": "Espressif (IoT/cam)", "C4:4F:33": "Espressif (IoT/cam)",
}


def _local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _ssid_atual() -> str | None:
    try:
        out = subprocess.run(["netsh", "wlan", "show", "interfaces"],
                             capture_output=True, text=True, errors="ignore", timeout=5).stdout or ""
        m = re.search(r"^\s*SSID\s*:\s*(.+)$", out, re.M)
        return m.group(1).strip() if m else None
    except (OSError, subprocess.SubprocessError):
        return None


def info_rede() -> dict:
    ip = _local_ip()
    try:
        net = ipaddress.ip_network(ip + "/24", strict=False)
        subnet = str(net)
    except ValueError:
        subnet = ip + "/24"
    return {"ip_local": ip, "ssid": _ssid_atual(), "subnet": subnet}


def _ping(ip: str) -> str | None:
    try:
        r = subprocess.run(["ping", "-n", "1", "-w", "350", ip],
                           capture_output=True, text=True, errors="ignore", timeout=2)
        out = r.stdout or ""
        # responde se houve reply (TTL no texto) ou exit 0 com tempo
        if "TTL=" in out or "ttl=" in out:
            return ip
        return None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _arp_table() -> dict:
    macs = {}
    try:
        out = subprocess.run(["arp", "-a"], capture_output=True, text=True,
                             errors="ignore", timeout=6).stdout or ""
        for line in out.splitlines():
            m = re.search(r"(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F]{2}[-:][0-9a-fA-F]{2}[-:][0-9a-fA-F]{2}[-:][0-9a-fA-F]{2}[-:][0-9a-fA-F]{2}[-:][0-9a-fA-F]{2})", line)
            if m:
                macs[m.group(1)] = m.group(2).upper().replace("-", ":")
    except (OSError, subprocess.SubprocessError):
        pass
    return macs


def _porta_aberta(ip: str, port: int, timeout: float = 0.5) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex((ip, port)) == 0
    except OSError:
        return False
    finally:
        s.close()


def _hostname(ip: str) -> str:
    try:
        return socket.gethostbyaddr(ip)[0]
    except (OSError, socket.herror):
        return ""


def _vendor(mac: str) -> str:
    if not mac:
        return ""
    return OUI_CAM.get(mac[:8], "")


def _classificar(vendor: str, portas: list) -> tuple[bool, int, str]:
    ps = set(portas)
    if ps & RTSP_PORTS:
        return True, 3, "porta RTSP aberta (stream de vídeo)"
    if vendor and (ps & ONVIF_PORTS):
        return True, 3, f"fabricante de câmera ({vendor}) + porta web"
    if vendor:
        return True, 2, f"fabricante de câmera ({vendor})"
    if 37777 in ps or 34567 in ps:
        return True, 2, "porta de DVR/câmera (Dahua/genérico)"
    if ps & ONVIF_PORTS and (8000 in ps or 8080 in ps):
        return False, 1, "porta web — pode ser câmera ou outro dispositivo"
    return False, 0, ""


def escanear() -> dict:
    info = info_rede()
    try:
        net = ipaddress.ip_network(info["subnet"], strict=False)
    except ValueError:
        return {"ok": False, "erro": "não foi possível determinar a sub-rede", **info}

    hosts = [str(h) for h in net.hosts()]
    # 1) ping sweep concorrente para popular a tabela ARP
    with ThreadPoolExecutor(max_workers=100) as ex:
        vivos = [r for r in ex.map(_ping, hosts) if r]

    arp = _arp_table()
    for ip, mac in list(arp.items()):
        # ignora broadcast/multicast (ex.: x.x.x.255, 224-239.x, MAC FF:FF / 01:00:5E)
        if mac.startswith("FF:FF:FF") or mac.startswith("01:00:5E") or mac.startswith("33:33"):
            arp.pop(ip, None)
            continue
        try:
            addr = ipaddress.ip_address(ip)
            if addr in net and ip not in vivos and ip != str(net.broadcast_address):
                vivos.append(ip)
        except ValueError:
            pass
    vivos = [ip for ip in vivos if ip != str(net.broadcast_address)]

    def _detalhar(ip: str) -> dict:
        mac = arp.get(ip, "")
        vendor = _vendor(mac)
        # varre portas de câmera em paralelo
        with ThreadPoolExecutor(max_workers=len(CAM_PORTS)) as ex:
            res = list(ex.map(lambda p: (p, _porta_aberta(ip, p)), CAM_PORTS))
        portas = [p for p, ok in res if ok]
        host = _hostname(ip)
        cam, conf, motivo = _classificar(vendor, portas)
        return {
            "ip": ip, "mac": mac, "vendor": vendor, "hostname": host,
            "portas": portas, "camera": cam, "conf": conf, "motivo": motivo,
            "url": f"http://{ip}" if (80 in portas or 8080 in portas or 8000 in portas) else "",
        }

    with ThreadPoolExecutor(max_workers=40) as ex:
        devs = list(ex.map(_detalhar, vivos))

    devs.sort(key=lambda d: (-d["conf"], _ip_key(d["ip"])))
    cams = [d for d in devs if d["camera"]]
    return {
        "ok": True,
        **info,
        "n_dispositivos": len(devs),
        "n_cameras": len(cams),
        "dispositivos": devs,
    }


def _ip_key(ip: str):
    try:
        return tuple(int(x) for x in ip.split("."))
    except ValueError:
        return (999,)
