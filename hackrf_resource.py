"""
mtzRF — HackRF Resource Manager
===================================
Garante acesso EXCLUSIVO ao HackRF One: apenas um processo usa o
dispositivo de cada vez. Qualquer chamada a hackrf_transfer,
hackrf_sweep ou grgsm deve passar por este módulo.

API pública
-----------
    acquire(nome, timeout)  → bool      adquire o lock
    release()                           libera o lock
    zerar()                             mata TUDO e reseta
    dono()                  → str|None  quem tem o lock agora
    livre()                 → bool      True se disponível
"""

import sys
import threading
import subprocess
import time
from typing import Optional


# ── Kill cross-platform ──────────────────────────────────────────────────────────
def matar(*nomes: str):
    """Mata processos por nome, funcionando em Windows, macOS e Linux.

    No Windows usa taskkill por nome de imagem (.exe); em Unix usa pkill -f.
    Erros (binário ausente, processo inexistente) são ignorados.
    """
    for nome in nomes:
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/T", "/IM", f"{nome}.exe"],
                               capture_output=True)
            else:
                subprocess.run(["pkill", "-9", "-f", nome], capture_output=True)
        except (OSError, subprocess.SubprocessError):
            pass

# ── Estado global ──────────────────────────────────────────────────────────────
_lock   = threading.Lock()
_owner: Optional[str] = None
_ts:    float          = 0.0


# ── API principal ──────────────────────────────────────────────────────────────

def acquire(nome: str, timeout: float = 30.0) -> bool:
    """
    Tenta adquirir acesso exclusivo ao HackRF.

    Parâmetros
    ----------
    nome    : identificador do chamador (ex: 'doppler', 'espectro', 'radio')
    timeout : tempo máximo de espera em segundos

    Retorna True se conseguiu, False se expirou o timeout.
    """
    global _owner, _ts
    adquiriu = _lock.acquire(timeout=timeout)
    if adquiriu:
        _owner = nome
        _ts    = time.time()
    return adquiriu


def release():
    """Libera o HackRF para o próximo processo."""
    global _owner, _ts
    _owner = None
    _ts    = 0.0
    try:
        _lock.release()
    except RuntimeError:
        pass  # já liberado — sem problema


def zerar():
    """
    Mata TODOS os processos que usam o HackRF e reseta o estado.
    Use antes de iniciar qualquer atividade exclusiva (IMSI, radio, tx).
    """
    matar('hackrf_transfer', 'hackrf_sweep', 'grgsm')
    time.sleep(0.6)   # aguarda kernel fechar os file descriptors do USB
    # Se o lock estiver preso (processo morreu sem liberar), força reset
    global _owner, _ts
    if _owner is not None:
        _owner = None
        _ts    = 0.0
        try:
            _lock.release()
        except RuntimeError:
            pass


def dono() -> Optional[str]:
    """Retorna o nome do detentor atual (ou None se livre)."""
    return _owner


def livre() -> bool:
    """True se o HackRF está disponível agora."""
    return _owner is None


def tempo_uso() -> float:
    """Segundos que o detentor atual usa o dispositivo (0 se livre)."""
    return time.time() - _ts if _owner else 0.0
