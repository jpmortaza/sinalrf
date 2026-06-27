#!/usr/bin/env python3
"""
mtzRF — Histórico e Relatórios
==============================
Salva varreduras (TSCM, WiFi, câmeras IP, etc.) em disco e gera relatórios HTML
profissionais (imprimíveis em PDF pelo navegador).

Cada entrada é um JSON em historico/. Os relatórios são gerados sob demanda.
"""

import re
import json
import time
import html
from pathlib import Path

_DIR = Path(__file__).parent / "historico"
_DIR.mkdir(exist_ok=True)


def _safe(eid: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "", eid)[:64]


def _novo_id(tipo: str) -> str:
    base = re.sub(r"[^a-z0-9]", "", (tipo or "scan").lower())[:8] or "scan"
    return time.strftime("%Y%m%d-%H%M%S") + "-" + base


def _resumo(d: dict) -> str:
    dados = d.get("dados") or {}
    t = d.get("tipo", "")
    if "sinais" in dados:
        n = len(dados.get("sinais") or [])
        s = sum(1 for x in (dados.get("sinais") or []) if x.get("suspeita", 0) >= 2)
        return f"{n} sinais · {s} suspeitos"
    if "redes" in dados:
        n = len(dados.get("redes") or [])
        r = len(dados.get("rogue") or [])
        return f"{n} APs · {r} alertas rogue"
    if "dispositivos" in dados:
        n = len(dados.get("dispositivos") or [])
        c = sum(1 for x in (dados.get("dispositivos") or []) if x.get("camera"))
        return f"{n} dispositivos · {c} câmeras"
    return t


# ── CRUD ──────────────────────────────────────────────────────────────────────────
def salvar(tipo: str, titulo: str, dados: dict) -> dict:
    eid = _novo_id(tipo)
    item = {
        "id": eid,
        "tipo": tipo or "scan",
        "titulo": (titulo or tipo or "Varredura").strip(),
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dados": dados or {},
    }
    (_DIR / f"{eid}.json").write_text(json.dumps(item, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "id": eid}


def listar() -> list:
    out = []
    for f in sorted(_DIR.glob("*.json"), reverse=True):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            out.append({"id": d["id"], "tipo": d["tipo"], "titulo": d["titulo"],
                        "ts": d["ts"], "resumo": _resumo(d)})
        except (ValueError, OSError, KeyError):
            pass
    return out


def obter(eid: str) -> dict | None:
    f = _DIR / f"{_safe(eid)}.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def excluir(eid: str) -> bool:
    f = _DIR / f"{_safe(eid)}.json"
    if f.exists():
        try:
            f.unlink()
            return True
        except OSError:
            return False
    return False


# ── Relatório HTML ────────────────────────────────────────────────────────────────
def _tabela(itens: list, colunas: list) -> str:
    if not itens:
        return "<p class='vazio'>Nenhum registro.</p>"
    th = "".join(f"<th>{html.escape(c[1])}</th>" for c in colunas)
    linhas = []
    for it in itens:
        tds = "".join(f"<td>{html.escape(str(it.get(c[0], '')))}</td>" for c in colunas)
        linhas.append(f"<tr>{tds}</tr>")
    return f"<table><thead><tr>{th}</tr></thead><tbody>{''.join(linhas)}</tbody></table>"


def _corpo(d: dict) -> str:
    dados = d.get("dados") or {}
    if "sinais" in dados:
        cols = [("freq_mhz", "Freq (MHz)"), ("dbm", "Pot (dBm)"), ("bw_khz", "BW (kHz)"),
                ("cat", "Tipo"), ("suspeita", "Suspeita"), ("desc", "Análise")]
        return f"<h2>Sinais detectados ({len(dados.get('sinais') or [])})</h2>" + _tabela(dados.get("sinais") or [], cols)
    if "redes" in dados:
        rogue = dados.get("rogue") or []
        alertas = "".join(f"<div class='alerta'>⚠ {html.escape(a.get('ssid',''))} — {html.escape(a.get('motivo',''))}</div>" for a in rogue)
        cols = [("ssid", "SSID"), ("bssid", "BSSID"), ("sinal", "Sinal %"), ("canal", "Canal"), ("seg", "Segurança")]
        return (f"<h2>Alertas de rogue AP ({len(rogue)})</h2>{alertas or '<p class=vazio>Nenhum</p>'}"
                f"<h2>Redes ({len(dados.get('redes') or [])})</h2>" + _tabela(dados.get("redes") or [], cols))
    if "dispositivos" in dados:
        cols = [("ip", "IP"), ("mac", "MAC"), ("vendor", "Fabricante"), ("hostname", "Hostname"),
                ("portas", "Portas"), ("motivo", "Observação")]
        return f"<h2>Dispositivos ({len(dados.get('dispositivos') or [])})</h2>" + _tabela(dados.get("dispositivos") or [], cols)
    return "<h2>Dados</h2><pre>" + html.escape(json.dumps(dados, ensure_ascii=False, indent=2)) + "</pre>"


def relatorio_html(eid: str) -> str | None:
    d = obter(eid)
    if not d:
        return None
    corpo = _corpo(d)
    return f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8">
<title>Relatório mtzRF — {html.escape(d['titulo'])}</title>
<style>
  body{{font-family:'Segoe UI',Arial,sans-serif;color:#111;max-width:900px;margin:0 auto;padding:32px;}}
  .cab{{border-bottom:3px solid #1a6b38;padding-bottom:14px;margin-bottom:20px;display:flex;justify-content:space-between;align-items:flex-end;}}
  .cab h1{{font-size:22px;color:#1a6b38;letter-spacing:2px;}}
  .meta{{color:#666;font-size:13px;text-align:right;}}
  h2{{font-size:15px;margin:22px 0 8px;color:#1a6b38;border-bottom:1px solid #ddd;padding-bottom:4px;}}
  table{{width:100%;border-collapse:collapse;font-size:12px;margin-bottom:10px;}}
  th{{text-align:left;background:#f3f6f3;padding:6px 8px;border:1px solid #ddd;}}
  td{{padding:5px 8px;border:1px solid #eee;}}
  .alerta{{background:#fff3f3;border:1px solid #f0b0b0;color:#a00;padding:6px 10px;border-radius:4px;margin:4px 0;font-size:12px;}}
  .vazio{{color:#999;font-size:12px;}}
  pre{{background:#f7f7f7;padding:12px;border-radius:6px;font-size:11px;overflow:auto;}}
  .rod{{margin-top:30px;border-top:1px solid #ddd;padding-top:10px;color:#999;font-size:11px;}}
  @media print{{ body{{padding:0;}} .noprint{{display:none;}} }}
</style></head><body>
  <div class="cab">
    <div><h1>📡 mtzRF — RELATÓRIO</h1><div style="color:#333;font-size:14px;margin-top:4px">{html.escape(d['titulo'])}</div></div>
    <div class="meta">Tipo: {html.escape(d['tipo'])}<br>Data: {html.escape(d['ts'])}<br>ID: {html.escape(d['id'])}</div>
  </div>
  <button class="noprint" onclick="window.print()" style="margin-bottom:16px;padding:8px 16px;cursor:pointer">🖨 Imprimir / Salvar PDF</button>
  {corpo}
  <div class="rod">Gerado por mtzRF · Plataforma de sensoriamento RF · uso autorizado/responsável</div>
</body></html>"""
