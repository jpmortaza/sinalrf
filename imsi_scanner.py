"""
mtzRF — IMSI / TMSI Scanner (Passivo)
Detecta torres GSM com hackrf_sweep e captura identidades via gr-gsm.

AVISO LEGAL: Uso exclusivamente educacional / pesquisa.
A captação de comunicações eletrônicas pode ser crime — verifique a lei local.
Este módulo analisa apenas broadcasts GSM abertos (não criptografados).

Dependências:
  - hackrf          (brew install hackrf)
  - gr-gsm          (duplo clique em '📱 Instalar gr-gsm.command' — não disponível via brew)
"""

import subprocess
import threading
import socket
import struct
import time
import numpy as np
from collections import deque
from typing import Optional
import hackrf_resource

# ── Bandas GSM downlink (torres → celulares) ──────────────────────────────────
GSM_BANDS = [
    # (nome, f_min MHz, f_max MHz, arfcn_offset)
    ("GSM-850",  869.0,  894.0, 128),
    ("GSM-900",  935.0,  960.0,   0),
    ("DCS-1800",1805.0, 1880.0, 512),
]

# ── Operadoras Brasil MCC=724 ──────────────────────────────────────────────────
OPERADORAS = {
    "72402": ("TIM",          "#FF4444"),
    "72403": ("TIM",          "#FF4444"),
    "72404": ("TIM",          "#FF4444"),
    "72405": ("CLARO",        "#FF6600"),
    "72406": ("VIVO",         "#AA00FF"),
    "72410": ("VIVO",         "#AA00FF"),
    "72411": ("VIVO",         "#AA00FF"),
    "72415": ("SERCOMTEL",    "#00AAFF"),
    "72416": ("OI",           "#FFDD00"),
    "72423": ("VIVO",         "#AA00FF"),
    "72431": ("OI",           "#FFDD00"),
    "72432": ("OI",           "#FFDD00"),
    "72434": ("NEXTEL",       "#00FF88"),
    "72439": ("SERCOMTEL",    "#00AAFF"),
}

def _operadora(mcc: str, mnc: str) -> tuple[str, str]:
    """Retorna (nome, cor) da operadora ou genérico."""
    key = mcc + mnc.zfill(2)
    return OPERADORAS.get(key, (f"MCC{mcc}/MNC{mnc}", "#666666"))

# ── GSMTAP ─────────────────────────────────────────────────────────────────────
GSMTAP_UDP_PORT = 4729
GSMTAP_T_UM     = 0x01   # GSM Um interface

# Offsets absolutos no UDP payload (confirmar com análise oros42/imsi-catcher)
# GSMTAP header = 16 bytes → LAPDm pseudo-len (1) + address (1) → L3 começa em 0x12
L3_PD_OFF = 0x11   # Protocol Discriminator byte
L3_MT_OFF = 0x12   # Message Type byte

# Message Types
MT_SI3          = 0x1B  # System Information Type 3  (PD=6/RR)
MT_SI1          = 0x19  # System Information Type 1  (PD=6/RR)
MT_PAGING_1     = 0x21  # Paging Request Type 1       (PD=6/RR)
MT_PAGING_2     = 0x22  # Paging Request Type 2       (PD=6/RR)
MT_PAGING_3     = 0x23  # Paging Request Type 3       (PD=6/RR)
MT_ID_RESP      = 0x19  # Identity Response            (PD=5/MM)
MT_LOC_UPD      = 0x08  # Location Update Request      (PD=5/MM)

TIPO_IMSI = 0x01
TIPO_IMEI = 0x02
TIPO_TMSI = 0x04


def _bcd(raw: bytes) -> str:
    """Decodifica BCD swapped → string IMSI/IMEI."""
    s = ''
    for b in raw:
        s += str(b & 0x0F) + str((b >> 4) & 0x0F)
    return s.replace('f', '').replace('F', '')


def _hexstr(raw: bytes) -> str:
    return raw.hex().upper()


class ScannerIMSI:
    """
    Scanner passivo de IMSI/TMSI.
    Fase 1: hackrf_sweep nas bandas GSM → lista de torres.
    Fase 2: grgsm_livemon_headless na torre mais forte → UDP:4729 → parse L3.
    """

    def __init__(self, sensor_hackrf=None, sensor_espectro=None, sensor_intel=None):
        self.hackrf_ok    = False
        self.grgsm_ok     = False
        self.grgsm_cmd    = None
        self.capturando   = False
        self.escaneando   = False
        self._sensor_hackrf  = sensor_hackrf    # doppler/scan loops
        self._sensor_espectro = sensor_espectro  # hackrf_sweep wideband
        self._sensor_intel   = sensor_intel      # hackrf_sweep 88–6000 MHz

        self.torres:   list[dict] = []
        self.capturas: deque      = deque(maxlen=1000)
        self.celulas:  dict[int, dict] = {}   # arfcn → {mcc, mnc, lac, cid}

        self._stats      = {"total": 0, "imsi": 0, "tmsi": 0, "imei": 0}
        self._vistos:    set = set()
        self._freq_atual: Optional[float] = None   # frequência que grgsm está tentando agora

        self._lock         = threading.Lock()
        self._parar        = threading.Event()
        self._grgsm_proc:  Optional[subprocess.Popen] = None
        self._thread_cap:  Optional[threading.Thread] = None

        self._verificar()

    # ── Verificação de dependências ────────────────────────────────────────────
    def _verificar(self):
        try:
            r = subprocess.run(['hackrf_info'], capture_output=True, text=True, timeout=3)
            self.hackrf_ok = 'Serial number' in r.stdout or 'Found HackRF' in r.stdout
        except (OSError, subprocess.TimeoutExpired):
            pass

        import os
        _here = os.path.dirname(os.path.abspath(__file__))
        _local = os.path.join(_here, 'grgsm_fixed.py')

        candidates = []
        if os.path.exists(_local):
            # roda direto pelo shebang (#!/opt/homebrew/bin/python3 que tem gnuradio)
            os.chmod(_local, 0o755)
            candidates.append((_local,))
        candidates += [('grgsm_livemon_headless',), ('grgsm_livemon',)]

        for cand in candidates:
            try:
                subprocess.run(list(cand) + ['--help'], capture_output=True, timeout=3)
                self.grgsm_ok  = True
                self.grgsm_cmd = list(cand)   # lista: ['python3', '/path/grgsm_fixed.py'] ou ['grgsm_livemon_headless']
                break
            except (OSError, subprocess.TimeoutExpired):
                pass

        print(f"  📱  IMSI Scanner — HackRF={'✓' if self.hackrf_ok else '✗'}  "
              f"gr-gsm={'✓' if self.grgsm_ok else '✗'}")

    # ── API pública ────────────────────────────────────────────────────────────
    def scan_torres(self):
        """Varre bandas GSM em background e popula self.torres."""
        if self.escaneando:
            return
        def _pausar_e_varrer():
            self._pausar_todos_sensores()   # libera HackRF para hackrf_sweep
            self._scan_torres()
            self._retomar_todos_sensores()
        t = threading.Thread(target=_pausar_e_varrer, daemon=True, name="imsi-scan")
        t.start()

    def iniciar_captura(self, freq_mhz: Optional[float] = None):
        """Inicia grgsm + escuta UDP para capturar IMSI/TMSI."""
        if self.capturando:
            return
        self.capturando = True   # seta imediatamente — evita race condition com múltiplos cliques
        self._parar.clear()
        self._thread_cap = threading.Thread(
            target=self._loop_captura, args=(freq_mhz,), daemon=True, name="imsi-cap"
        )
        self._thread_cap.start()

    def parar_captura(self):
        self._parar.set()
        self._parar_grgsm()
        self.capturando = False

    def limpar(self):
        with self._lock:
            self.capturas.clear()
            self._vistos.clear()
            self._stats = {"total": 0, "imsi": 0, "tmsi": 0, "imei": 0}

    # ── Scan de torres ─────────────────────────────────────────────────────────
    def _scan_torres(self):
        if not self.hackrf_ok:
            return
        self.escaneando = True
        novas: list[dict] = []

        for banda, f_min, f_max, arfcn_off in GSM_BANDS:
            try:
                proc = subprocess.run(
                    ['hackrf_sweep',
                     '-f', f'{int(f_min)}:{int(f_max)}',
                     '-l', '32', '-g', '40',
                     '-w', '200000',   # 200 kHz = 1 ARFCN por bin
                     '-N', '1', '-r', '-'],
                    capture_output=True, timeout=90
                )
                if not proc.stdout:
                    continue

                freqs, dbms = [], []
                for ln in proc.stdout.decode('utf-8', errors='ignore').splitlines():
                    ps = ln.strip().split(',')
                    if len(ps) < 7:
                        continue
                    try:
                        hz_low = float(ps[2])
                        bin_w  = float(ps[4])
                        for i, v in enumerate([float(x) for x in ps[6:] if x.strip()]):
                            f = (hz_low + bin_w * (i + 0.5)) / 1e6
                            if f_min <= f <= f_max:
                                freqs.append(f)
                                dbms.append(v)
                    except (ValueError, IndexError):
                        continue

                if len(freqs) < 3:
                    continue

                fa, da = np.array(freqs), np.array(dbms)
                for i in range(3, len(fa) - 3):
                    if da[i] > -80 and float(da[i]) >= float(da[i-3:i+4].max()) - 0.01:
                        arfcn_idx = round((float(fa[i]) - f_min) / 0.2)
                        arfcn     = arfcn_off + arfcn_idx
                        # Snap para frequência GSM válida (múltiplo de 200 kHz a partir de f_min)
                        freq_snap = round(f_min + arfcn_idx * 0.2, 1)
                        novas.append({
                            'banda': banda, 'freq_mhz': freq_snap,
                            'arfcn': arfcn, 'dbm': round(float(da[i]), 1),
                            'mcc': '', 'mnc': '', 'lac': 0, 'cid': 0,
                            'operadora': '—', 'cor': '#666666', 'ts': time.time(),
                        })
            except (subprocess.TimeoutExpired, Exception) as e:
                print(f'  [IMSI] scan {banda}: {e}')

        novas.sort(key=lambda x: x['dbm'], reverse=True)
        with self._lock:
            self.torres = novas[:30]
        print(f"  📱  IMSI: {len(novas)} torres em {len(GSM_BANDS)} bandas")
        self.escaneando = False

    # ── Captura IMSI via gr-gsm ────────────────────────────────────────────────
    def _loop_captura(self, freq_mhz: Optional[float]):
        if not self.grgsm_ok:
            self.capturando = False
            return

        import os as _os
        _log_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'grgsm.log')

        # Zera TUDO e garante acesso exclusivo ao HackRF
        hackrf_resource.zerar()
        self._pausar_todos_sensores()

        # Monta lista de frequências a tentar
        if freq_mhz is not None:
            # Usuário especificou: tenta somente esta
            lista_tentar = [freq_mhz]
        else:
            with self._lock:
                torres = list(self.torres)
            if not torres:
                self._scan_torres()
                time.sleep(4)
                with self._lock:
                    torres = list(self.torres)
            if not torres:
                print('  [IMSI] nenhuma torre encontrada')
                self.capturando = False
                self._retomar_todos_sensores()
                return
            # Top-20 torres mais fortes; inclui todas as bandas
            lista_tentar = [t['freq_mhz'] for t in torres[:20]]

        TIMEOUT_SEM_GSM = 15   # segundos por torre no modo auto-scan

        for fmhz in lista_tentar:
            if self._parar.is_set():
                break

            freq_hz = int(fmhz * 1e6)
            print(f"  📱  IMSI: tentando {fmhz} MHz…")
            with self._lock:
                self._freq_atual = fmhz

            # Mata grgsm anterior e libera porta
            subprocess.run(['pkill', '-9', '-f', 'grgsm'], capture_output=True)
            subprocess.run('lsof -ti :4729 | xargs kill -9 2>/dev/null',
                           shell=True, capture_output=True)
            time.sleep(1.0)

            # Adquire lock exclusivo para o grgsm (pode durar minutos)
            if not hackrf_resource.acquire('grgsm', timeout=5.0):
                hackrf_resource.zerar()
                hackrf_resource.acquire('grgsm', timeout=3.0)

            _log = open(_log_path, 'w')
            try:
                self._grgsm_proc = subprocess.Popen(
                    self.grgsm_cmd + [
                        f'--fc={freq_hz}',
                        '--gain=40',
                        '--samp-rate=2000000',
                        '--serverport=4730'],
                    stdout=_log, stderr=_log,
                )
            except Exception as e:
                print(f'  [IMSI] erro grgsm: {e}')
                hackrf_resource.release()
                _log.close()
                continue

            time.sleep(2)   # grgsm precisa inicializar

            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except AttributeError:
                    pass
                sock.bind(('0.0.0.0', GSMTAP_UDP_PORT))
                sock.settimeout(1.0)
            except Exception as e:
                print(f'  [IMSI] erro UDP em {fmhz} MHz: {e}')
                self._parar_grgsm()
                _log.close()
                continue

            print(f'  📱  IMSI: escutando {fmhz} MHz (UDP:{GSMTAP_UDP_PORT})')
            t_inicio   = time.time()
            recebeu    = False

            while not self._parar.is_set():
                try:
                    data, _ = sock.recvfrom(4096)
                    recebeu = True
                    self._processar(data)
                except socket.timeout:
                    if self._grgsm_proc and self._grgsm_proc.poll() is not None:
                        print(f'  [IMSI] grgsm encerrou em {fmhz} MHz')
                        break
                    # Auto-scan: sem pacote no prazo → próxima torre
                    if freq_mhz is None and not recebeu:
                        if time.time() - t_inicio > TIMEOUT_SEM_GSM:
                            print(f'  [IMSI] sem GSM em {fmhz} MHz — próxima torre')
                            break
                except Exception:
                    break

            sock.close()
            self._parar_grgsm()
            hackrf_resource.release()   # devolve HackRF após cada tentativa
            _log.close()

            if recebeu or self._parar.is_set():
                break   # encontrou GSM real (ou usuário parou)

        with self._lock:
            self._freq_atual = None
        self.capturando = False
        self._retomar_todos_sensores()

    # ── Parsing GSMTAP / GSM L3 ────────────────────────────────────────────────
    def _processar(self, data: bytes):
        if len(data) < 20:
            return

        # GSMTAP header (RFC-like): version, hdr_len (×4 bytes), type, timeslot, arfcn…
        hdr_len   = data[1] * 4
        gsm_type  = data[2]
        arfcn     = struct.unpack('>H', data[4:6])[0] & 0x7FFF
        signal_db = struct.unpack('>b', bytes([data[6]]))[0]

        if gsm_type != GSMTAP_T_UM or len(data) <= hdr_len + 3:
            return

        # Debug: imprime primeiros 15 pacotes para calibrar offsets
        if not hasattr(self, '_dbg_n'):
            self._dbg_n = 0
        if self._dbg_n < 15:
            hex_s = data[:36].hex()
            print(f"  [IMSI PKT#{self._dbg_n}] {len(data)}B hdr={hdr_len} arfcn={arfcn} "
                  f"dbm={signal_db}  {hex_s}")
            self._dbg_n += 1

        # Detecta offset L3 experimentando 0‒3 bytes de LAPDm após o header
        l2 = data[hdr_len:]
        pd = mt = 0
        cs = -1   # content_start = posição do 1º byte após PD+MT
        for skip in (2, 3, 1, 0):
            if len(l2) > skip + 1:
                _pd = l2[skip] & 0x0F
                _mt = l2[skip + 1]
                if _pd in (0x05, 0x06):   # MM ou RR
                    pd, mt = _pd, _mt
                    cs = hdr_len + skip + 2  # byte após MT
                    break

        if cs < 0 or len(data) <= cs:
            return

        if mt == MT_SI3 and len(data) > cs + 7:
            self._si3(data, cs, arfcn, signal_db)
        elif mt == MT_PAGING_1 and len(data) > cs + 4:
            self._paging1(data, cs, arfcn, signal_db)
        elif mt == MT_PAGING_2 and len(data) > cs + 8:
            self._paging2(data, cs, arfcn, signal_db)
        elif mt == MT_PAGING_3 and len(data) > cs + 16:
            self._paging3(data, cs, arfcn, signal_db)
        elif pd == 0x05 and mt == MT_ID_RESP and len(data) > cs + 1:
            self._id_resp(data, cs, arfcn, signal_db)
        elif pd == 0x05 and mt == MT_LOC_UPD and len(data) > cs + 8:
            self._loc_upd(data, cs, arfcn, signal_db)

    # Todos os métodos abaixo recebem `cs` = posição absoluta do 1º byte de conteúdo L3
    # (byte imediatamente após o Message Type)

    def _si3(self, p, cs, arfcn, dbm):
        """System Information Type 3 → extrai MCC, MNC, LAC, CID."""
        try:
            cid   = struct.unpack('>H', p[cs:cs+2])[0]
            mcc1  = p[cs+2]; mcc2 = p[cs+3]; mnc_b = p[cs+4]
            lac   = struct.unpack('>H', p[cs+5:cs+7])[0]
            mcc   = f"{mcc1 & 0xF}{(mcc1>>4)&0xF}{mcc2&0xF}"
            mnc_s = f"{mnc_b&0xF}{(mnc_b>>4)&0xF}".rstrip('f')
            nome, cor = _operadora(mcc, mnc_s)
            with self._lock:
                self.celulas[arfcn] = {
                    'arfcn': arfcn, 'cid': cid, 'lac': lac,
                    'mcc': mcc, 'mnc': mnc_s, 'operadora': nome, 'cor': cor,
                }
                for t in self.torres:
                    if t.get('arfcn') == arfcn:
                        t.update({'mcc': mcc, 'mnc': mnc_s, 'lac': lac,
                                  'cid': cid, 'operadora': nome, 'cor': cor})
        except Exception:
            pass

    def _paging1(self, p, cs, arfcn, dbm):
        """Paging Request Type 1 — até 2 identidades (IMSI ou TMSI)."""
        # cs[0] = page mode, cs[1] = channel needed
        # cs[2] = comprimento da 1ª identidade
        if len(p) <= cs + 2:
            return
        id1_len = p[cs + 2]
        id1_off = cs + 3
        if len(p) > id1_off:
            id_t = p[id1_off] & 0x07
            if id_t == TIPO_IMSI and (p[id1_off] & 0x01) and len(p) >= id1_off + id1_len:
                self._reg('IMSI', _bcd(p[id1_off: id1_off + id1_len]), arfcn, dbm)
            elif id_t == TIPO_TMSI and len(p) >= id1_off + 5:
                self._reg('TMSI', _hexstr(p[id1_off+1: id1_off+5]), arfcn, dbm)

        # 2ª identidade (após 1ª)
        id2_off = id1_off + id1_len
        if len(p) > id2_off + 1:
            id2_len = p[id2_off]
            id2_val = id2_off + 1
            if len(p) > id2_val:
                id_t = p[id2_val] & 0x07
                if id_t == TIPO_IMSI and (p[id2_val] & 0x01) and len(p) >= id2_val + id2_len:
                    self._reg('IMSI', _bcd(p[id2_val: id2_val + id2_len]), arfcn, dbm)
                elif id_t == TIPO_TMSI and len(p) >= id2_val + 5:
                    self._reg('TMSI', _hexstr(p[id2_val+1: id2_val+5]), arfcn, dbm)

    def _paging2(self, p, cs, arfcn, dbm):
        """Paging Type 2 — 2 TMSIs + IMSI opcional."""
        if len(p) > cs + 4:  self._reg('TMSI', _hexstr(p[cs:cs+4]),    arfcn, dbm)
        if len(p) > cs + 8:  self._reg('TMSI', _hexstr(p[cs+4:cs+8]),  arfcn, dbm)
        # IMSI extra no final
        imsi_off = cs + 9
        if len(p) > imsi_off + 1:
            id_t = p[imsi_off] & 0x07
            id_len = p[imsi_off - 1] if imsi_off > 0 else 0
            if id_t == TIPO_IMSI and (p[imsi_off] & 0x01) and len(p) >= imsi_off + id_len:
                self._reg('IMSI', _bcd(p[imsi_off: imsi_off + id_len]), arfcn, dbm)

    def _paging3(self, p, cs, arfcn, dbm):
        """Paging Type 3 — 4 TMSIs."""
        for i in range(4):
            off = cs + i * 4
            if len(p) > off + 4:
                self._reg('TMSI', _hexstr(p[off: off+4]), arfcn, dbm)

    def _id_resp(self, p, cs, arfcn, dbm):
        """Identity Response → IMSI, IMEI ou TMSI."""
        if len(p) <= cs: return
        id_t = p[cs] & 0x07
        if id_t == TIPO_IMSI and len(p) > cs + 8:
            self._reg('IMSI', _bcd(p[cs: cs+8]), arfcn, dbm)
        elif id_t == TIPO_IMEI and len(p) > cs + 8:
            self._reg('IMEI', _bcd(p[cs: cs+8]), arfcn, dbm)
        elif id_t == TIPO_TMSI and len(p) > cs + 5:
            self._reg('TMSI', _hexstr(p[cs+1: cs+5]), arfcn, dbm)

    def _loc_upd(self, p, cs, arfcn, dbm):
        """Location Update Request → IMSI ou TMSI."""
        # cs[0] = LU type; cs[1:6] = LAI (5 bytes); cs[6] = identity
        off = cs + 6
        if len(p) <= off: return
        id_t = p[off] & 0x07
        if id_t == TIPO_IMSI and len(p) > off + 8:
            self._reg('IMSI', _bcd(p[off: off+8]), arfcn, dbm)
        elif id_t == TIPO_TMSI and len(p) > off + 5:
            self._reg('TMSI', _hexstr(p[off+1: off+5]), arfcn, dbm)

    # ── Registro de captura ────────────────────────────────────────────────────
    def _reg(self, tipo: str, valor: str, arfcn: int, dbm: int):
        valor = valor.strip('fF').strip()
        if len(valor) < 5: return
        chave = f"{tipo}:{valor}"
        if chave in self._vistos: return
        self._vistos.add(chave)

        celula   = self.celulas.get(arfcn, {})
        mcc, mnc = celula.get('mcc', ''), celula.get('mnc', '')
        if tipo == 'IMSI' and len(valor) >= 6:
            mcc, mnc = valor[:3], valor[3:5]
        nome, cor = _operadora(mcc, mnc) if mcc else ('—', '#666666')

        entrada = {
            'tipo': tipo, 'valor': valor,
            'mcc': mcc, 'mnc': mnc,
            'operadora': nome, 'cor': cor,
            'arfcn': arfcn, 'dbm': dbm,
            'ts': time.time(),
        }
        with self._lock:
            self.capturas.appendleft(entrada)
            self._stats['total'] += 1
            self._stats[tipo.lower()] = self._stats.get(tipo.lower(), 0) + 1

        print(f"  📱  {tipo}: {valor[:15]}…  [{nome}]  ARFCN={arfcn}  {dbm}dBm")

    def _pausar_todos_sensores(self):
        """Pausa todos os scanners que usam HackRF para liberar o dispositivo ao grgsm."""
        print('  📱  IMSI: pausando todos os scanners HackRF…')
        if self._sensor_hackrf:
            self._sensor_hackrf.pausar()   # aguarda 0.8s internamente
        if self._sensor_espectro:
            self._sensor_espectro.pausar()
        if self._sensor_intel:
            self._sensor_intel.pausar()
        # Dá mais 0.5s para garantir que qualquer hackrf_sweep em curso termine
        time.sleep(0.5)

    def _retomar_todos_sensores(self):
        """Retoma todos os scanners após liberar o HackRF."""
        print('  📱  IMSI: retomando scanners HackRF…')
        if self._sensor_hackrf:
            self._sensor_hackrf.retomar()
        if self._sensor_espectro:
            self._sensor_espectro.retomar()
        if self._sensor_intel:
            self._sensor_intel.retomar()

    def _parar_grgsm(self):
        if self._grgsm_proc:
            try: self._grgsm_proc.terminate(); self._grgsm_proc.wait(timeout=3)
            except Exception: self._grgsm_proc.kill()
            self._grgsm_proc = None

    # ── Estado para API ────────────────────────────────────────────────────────
    def estado(self) -> dict:
        with self._lock:
            ops: dict[str, dict] = {}
            for c in self.capturas:
                op = c.get('operadora', '—')
                if op not in ops:
                    ops[op] = {'n': 0, 'cor': c.get('cor', '#666'), 'imsi': 0, 'tmsi': 0}
                ops[op]['n'] += 1
                ops[op][c['tipo'].lower()] = ops[op].get(c['tipo'].lower(), 0) + 1

            return {
                'hackrf_ok':  self.hackrf_ok,
                'grgsm_ok':   self.grgsm_ok,
                'grgsm_cmd':  ' '.join(self.grgsm_cmd) if isinstance(self.grgsm_cmd, list) else (self.grgsm_cmd or ''),
                'capturando':  self.capturando,
                'freq_atual':  self._freq_atual,
                'escaneando':  self.escaneando,
                'torres':     list(self.torres),
                'capturas':   list(self.capturas)[:200],
                'celulas':    {str(k): v for k, v in self.celulas.items()},
                'stats':      dict(self._stats),
                'n_unicos':   len(self._vistos),
                'operadoras': ops,
                'ts':         time.time(),
            }
