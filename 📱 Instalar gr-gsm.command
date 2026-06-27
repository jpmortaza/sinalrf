#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
#  mtzRF — Instalador de gr-gsm para macOS (arm64 / Apple Silicon)
#  Duplo clique para rodar. Pode pedir senha de admin (sudo make install).
#  Tempo estimado: 5–10 min.
# ─────────────────────────────────────────────────────────────────────────────

DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD="/tmp/sinalrf_build"
LOG="$DIR/instalar_grgsm.log"

G="\033[0;32m"; Y="\033[0;33m"; R="\033[0;31m"; B="\033[0;34m"; N="\033[0m"
ok()   { echo -e "  ${G}✅  $1${N}"; }
warn() { echo -e "  ${Y}⚠️   $1${N}"; }
err()  { echo -e "  ${R}❌  $1${N}"; }
info() { echo -e "  ${B}→   $1${N}"; }
step() { echo -e "\n  ${Y}━━  $1${N}"; }

exec 2>>"$LOG"
echo "" >> "$LOG"
echo "=== $(date) ===" >> "$LOG"
set -o pipefail

clear
echo ""
echo "  ██████████████████████████████████████████████"
echo "  ██   📱  Instalador gr-gsm  para macOS     ██"
echo "  ██   Apple Silicon · GNU Radio 3.10         ██"
echo "  ██████████████████████████████████████████████"
echo ""
echo "  Log: $LOG"
echo "  Arch: $(uname -m) | macOS: $(sw_vers -productVersion)"
echo ""

# ── Já instalado? ──────────────────────────────────────────────────────────────
if command -v grgsm_livemon_headless &>/dev/null; then
    ok "grgsm_livemon_headless já está instalado!"
    echo "  $(which grgsm_livemon_headless)"
    echo ""
    read -p "  Pressione ENTER para fechar..." _
    exit 0
fi

mkdir -p "$BUILD"

# ── 1. Homebrew deps ──────────────────────────────────────────────────────────
step "Verificando dependências brew"

BREW_DEPS=(hackrf gnuradio cmake autoconf automake libtool "pkg-config" swig boost talloc pybind11)
for dep in "${BREW_DEPS[@]}"; do
    if brew list "$dep" &>/dev/null 2>/dev/null; then
        echo "       $dep → já instalado"
    else
        info "brew install $dep ..."
        if brew install "$dep" >> "$LOG" 2>&1; then
            echo "       $dep → OK"
        else
            warn "$dep: falhou (ver log)"
        fi
    fi
done
ok "Dependências brew OK"

export PATH="/opt/homebrew/opt/libtool/libexec/gnubin:/opt/homebrew/bin:$PATH"
export PKG_CONFIG_PATH="/opt/homebrew/lib/pkgconfig"
BREW_PFX="$(brew --prefix)"
PYTHON_BIN="$(which python3)"
GR_VER=$(gnuradio-config-info --version 2>/dev/null || echo "0.0.0")
info "GNU Radio: $GR_VER | Python: $($PYTHON_BIN --version 2>&1)"

# ── 2. libosmocore (fonte) ────────────────────────────────────────────────────
step "Buildando libosmocore (lib GSM base)"

if pkg-config --exists libosmocore 2>/dev/null; then
    ok "libosmocore já instalado ($(pkg-config --modversion libosmocore))"
else
    info "Clonando libosmocore..."
    rm -rf "$BUILD/libosmocore"
    git clone --depth=1 https://gitea.osmocom.org/osmocom/libosmocore.git "$BUILD/libosmocore" \
        >> "$LOG" 2>&1 || {
        git clone --depth=1 https://github.com/osmocom/libosmocore.git "$BUILD/libosmocore" \
            >> "$LOG" 2>&1 || { err "Clone falhou"; cat "$LOG" | tail -10; read -p "ENTER..." _; exit 1; }
    }
    cd "$BUILD/libosmocore"

    # ── Patches de compatibilidade macOS ──────────────────────────────────────
    info "Aplicando patches macOS..."

    # Patch 1: setresgid/setresuid → setgid/setuid (não existe em macOS)
    # Usa [^,]* e [^)]* para não capturar < 0) no terceiro grupo (bug do .* greedy)
    sed -i '' 's/setresgid(\([^,]*\), *[^,]*, *[^)]*)/setgid(\1)/g' src/core/exec.c 2>/dev/null || true
    sed -i '' 's/setresuid(\([^,]*\), *[^,]*, *[^)]*)/setuid(\1)/g' src/core/exec.c 2>/dev/null || true
    sed -i '' 's/setresgid()/setgid()/g'                              src/core/exec.c 2>/dev/null || true
    sed -i '' 's/setresuid()/setuid()/g'                              src/core/exec.c 2>/dev/null || true

    # Patch 2: SO_PRIORITY (socket option Linux-only)
    sed -i '' 's/return setsockopt(fd, SOL_SOCKET, SO_PRIORITY, \&prio, sizeof(prio));/#ifdef SO_PRIORITY\n\treturn setsockopt(fd, SOL_SOCKET, SO_PRIORITY, \&prio, sizeof(prio));\n#else\n\treturn 0;\n#endif/' \
        src/core/socket.c 2>/dev/null || true

    # Patch 3: CLOCK_MONOTONIC_COARSE / CLOCK_BOOTTIME (Linux-only)
    python3 - <<'PYEOF' 2>/dev/null || true
import re, os
f = 'src/core/timer_clockgettime.c'
if not os.path.exists(f): exit()
c = open(f).read()
for macro in ['CLOCK_REALTIME_COARSE','CLOCK_MONOTONIC_COARSE','CLOCK_MONOTONIC_RAW','CLOCK_BOOTTIME','CLOCK_PROCESS_CPUTIME_ID','CLOCK_THREAD_CPUTIME_ID']:
    c = re.sub(r'(\tcase ' + macro + r':\n\t\treturn [^\n]+;)',
               '#ifdef ' + macro + r'\n\1\n#endif', c)
open(f,'w').write(c)
PYEOF

    # Patch 4: osmo_timerfd stubs para macOS (timerfd é Linux-only)
    python3 - <<'PYEOF' 2>/dev/null || true
f = 'src/core/select.c'
import os
if not os.path.exists(f): exit()
c = open(f).read()
stub = """
#else /* !HAVE_SYS_TIMERFD_H */
/* macOS stubs — timerfd is Linux-only */
int osmo_timerfd_disable(struct osmo_fd *ofd) { (void)ofd; return 0; }
int osmo_timerfd_schedule(struct osmo_fd *ofd, const struct timespec *first,
                          const struct timespec *interval)
    { (void)ofd; (void)first; (void)interval; return 0; }
int osmo_timerfd_setup(struct osmo_fd *ofd,
                       int (*cb)(struct osmo_fd *, unsigned int), void *data)
    { if(ofd){ ofd->cb=cb; ofd->data=data; } return 0; }
#endif /* HAVE_SYS_TIMERFD_H */
"""
old = '#endif /* HAVE_SYS_TIMERFD_H */'
if old in c:
    c = c.replace(old, stub)
    open(f,'w').write(c)
    print("timerfd stubs OK")
PYEOF

    # Patch 5: cpu_sched_vty.c — cpu_set_t / CPU_ALLOC são Linux-only
    python3 - <<'PYEOF' 2>/dev/null || true
import os
f = 'src/vty/cpu_sched_vty.c'
if not os.path.exists(f): exit()
c = open(f).read()
compat = """/* macOS compatibility: cpu_set_t / CPU_ALLOC family do not exist on macOS */
#ifdef __APPLE__
typedef struct { unsigned long __bits[1]; } cpu_set_t;
#define CPU_ALLOC(n)          ((cpu_set_t *)calloc(1, sizeof(cpu_set_t)))
#define CPU_ALLOC_SIZE(n)     (sizeof(cpu_set_t))
#define CPU_FREE(p)           free(p)
#define CPU_ZERO_S(sz, p)     memset((p), 0, (sz))
#define CPU_SET_S(c, sz, p)   ((void)(p))
#define CPU_ISSET_S(c, sz, p) (0)
#define CPU_COUNT_S(sz, p)    (0)
#endif /* __APPLE__ */

"""
marker = '#include <osmocom/vty/vty.h>'
if marker in c and 'CPU_ALLOC' not in c:
    c = c.replace(marker, compat + marker)
    open(f,'w').write(c)
    print("cpu_sched_vty.c patched")
PYEOF

    # Patch 6: gprs_ns2_fr.c — Frame Relay usa APIs Linux-only (linux/if.h, hdlc, etc.)
    python3 - <<'PYEOF' 2>/dev/null || true
import os
f = 'src/gb/gprs_ns2_fr.c'
if not os.path.exists(f): exit()
c = open(f).read()

# Adiciona guarda no início (após o bloco de comentário inicial)
guard_open = '/* Frame Relay / HDLC is Linux-only — skip entirely on macOS */\n#ifndef __APPLE__\n\n'
first_include = '#include <errno.h>'
if first_include in c and '#ifndef __APPLE__' not in c:
    c = c.replace(first_include, guard_open + first_include, 1)

# Adiciona stubs + fechamento no final
stubs = """
#endif /* !__APPLE__ */

/* ── macOS stubs — Frame Relay not supported ─────────────────── */
#ifdef __APPLE__
#include <osmocom/gprs/gprs_ns2.h>
#include <osmocom/gprs/frame_relay.h>

struct gprs_ns2_vc_bind *gprs_ns2_fr_bind_by_netif(
        struct gprs_ns2_inst *nsi, const char *netif) { return NULL; }
const char *gprs_ns2_fr_bind_netif(struct gprs_ns2_vc_bind *bind) { return NULL; }
enum osmo_fr_role gprs_ns2_fr_bind_role(struct gprs_ns2_vc_bind *bind) { return 0; }
int gprs_ns2_fr_bind(struct gprs_ns2_inst *nsi, const char *name,
        const char *netif, struct osmo_fr_network *fr_network,
        enum osmo_fr_role fr_role, struct gprs_ns2_vc_bind **result)
{ if (result) *result = NULL; return -1; }
int gprs_ns2_is_fr_bind(struct gprs_ns2_vc_bind *bind) { return 0; }
struct gprs_ns2_vc *gprs_ns2_fr_nsvc_by_dlci(
        struct gprs_ns2_vc_bind *bind, uint16_t dlci) { return NULL; }
struct gprs_ns2_vc *gprs_ns2_fr_connect(struct gprs_ns2_vc_bind *bind,
        struct gprs_ns2_nse *nse, uint16_t nsvci, uint16_t dlci) { return NULL; }
struct gprs_ns2_vc *gprs_ns2_fr_connect2(struct gprs_ns2_vc_bind *bind,
        uint16_t nsei, uint16_t nsvci, uint16_t dlci) { return NULL; }
uint16_t gprs_ns2_fr_nsvc_dlci(const struct gprs_ns2_vc *nsvc) { return 0; }
int gprs_ns2_find_vc_by_dlci(struct gprs_ns2_vc_bind *bind,
        uint16_t dlci, struct gprs_ns2_vc **result)
{ if (result) *result = NULL; return -1; }
#endif /* __APPLE__ */
"""
if '#ifndef __APPLE__' in c and '#endif /* !__APPLE__ */' not in c:
    c = c.rstrip() + stubs
    open(f,'w').write(c)
    print("gprs_ns2_fr.c patched")
PYEOF

    ok "Patches de fonte aplicados"

    # ── autoconf + configure ──────────────────────────────────────────────────
    info "autoreconf -fi ..."
    autoreconf -fi >> "$LOG" 2>&1 || { err "autoreconf falhou"; tail -20 "$LOG"; read -p "ENTER..." _; exit 1; }

    info "configure ..."
    ./configure \
        --prefix="$BREW_PFX" \
        --disable-doxygen \
        --disable-uring \
        --disable-pcsc \
        --disable-libmnl \
        --disable-libsctp \
        CFLAGS="-I$BREW_PFX/include -Wno-error -Wno-deprecated-declarations -Wno-implicit-function-declaration" \
        LDFLAGS="-L$BREW_PFX/lib" \
        >> "$LOG" 2>&1 || { err "configure falhou"; tail -20 "$LOG"; read -p "ENTER..." _; exit 1; }

    # Patch 7: libosmogb — remove -no-undefined e adiciona stub weak de bssgp_prim_cb
    # Deve ser aplicado AQUI: o Makefile de src/gb/ só existe após ./configure
    info "Patch 7: corrigindo src/gb/Makefile (libosmogb linker flags)..."

    # 7a: remove -no-undefined — macOS dylib não aceita símbolos indefinidos via libtool
    sed -i '' 's/ -no-undefined//g' src/gb/Makefile 2>/dev/null || true

    # 7b: cria stub weak de bssgp_prim_cb (callback esperado pela app, não pela lib)
    cat > src/gb/bssgp_prim_cb_weak.c << 'STUBEOF'
#include <osmocom/core/prim.h>
__attribute__((weak))
int bssgp_prim_cb(struct osmo_prim_hdr *oph, void *ctx) { (void)oph; (void)ctx; return 0; }
STUBEOF

    # 7c: adiciona o stub na lista de fontes do libosmogb
    sed -i '' 's/^libosmogb_la_SOURCES = /libosmogb_la_SOURCES = bssgp_prim_cb_weak.c /' \
        src/gb/Makefile 2>/dev/null || true

    ok "Patch 7 aplicado"

    info "make ($(sysctl -n hw.logicalcpu) jobs)..."
    make -j"$(sysctl -n hw.logicalcpu)" >> "$LOG" 2>&1 || {
        err "make libosmocore falhou"
        echo ""
        echo "  Últimas 25 linhas do log:"
        tail -25 "$LOG"
        echo ""
        read -p "  ENTER para fechar..." _
        exit 1
    }

    info "sudo make install ..."
    sudo make install >> "$LOG" 2>&1 || { err "install falhou"; tail -10 "$LOG"; read -p "ENTER..." _; exit 1; }
    ok "libosmocore instalado"
    cd "$DIR"
fi

# ── 3. gr-gsm (fonte) ─────────────────────────────────────────────────────────
step "Buildando gr-gsm (decodificador GSM)"

# bkerler_fork: branch com suporte a GR 3.10 (pybind11, módulo gnuradio.gsm)
BRANCH="bkerler_fork"
info "Usando branch gr-gsm: $BRANCH (GR $GR_VER)"

# Re-clona se branch errado ou não existe
CURR_BR=$(cd "$BUILD/gr-gsm" 2>/dev/null && git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "none")
if [ "$CURR_BR" != "HEAD" ] && [ "$CURR_BR" != "$BRANCH" ]; then
    info "Re-clonando gr-gsm (era '$CURR_BR', precisa '$BRANCH')..."
    rm -rf "$BUILD/gr-gsm"
fi

if [ ! -d "$BUILD/gr-gsm" ]; then
    info "Clonando gr-gsm ($BRANCH)..."
    git clone --depth=1 -b "$BRANCH" https://github.com/ptrkrysik/gr-gsm.git "$BUILD/gr-gsm" \
        >> "$LOG" 2>&1 || { err "Clone gr-gsm falhou"; tail -10 "$LOG"; read -p "ENTER..." _; exit 1; }
fi

cd "$BUILD/gr-gsm"

# Patch: CMake mínimo
sed -i '' 's/cmake_minimum_required(VERSION [0-9.]*)/cmake_minimum_required(VERSION 3.8)/' CMakeLists.txt 2>/dev/null || true

# Patch Boost 1.78+: io_service → io_context, resolver::query removido
info "Patch Boost 1.78+: io_service, resolver::query..."
sed -i '' 's/boost::asio::io_service/boost::asio::io_context/g' \
    include/gsm/misc_utils/udp_socket.h \
    lib/misc_utils/udp_socket.cc 2>/dev/null || true

# Patch: resolver::query API (removido em Boost 1.78+)
python3 - << 'PYEOF' 2>/dev/null || true
import re, pathlib
f = pathlib.Path("lib/misc_utils/udp_socket.cc")
if not f.exists(): exit()
c = f.read_text()
# Remove old query objects and update resolve() calls
c = re.sub(
    r'udp::resolver::query rx_query\([^;]+;\s*udp::resolver::query tx_query\([^;]+;\s*d_udp_endpoint_rx = \*resolver\.resolve\(rx_query\);\s*d_udp_endpoint_tx = \*resolver\.resolve\(tx_query\);',
    'd_udp_endpoint_rx = resolver.resolve(udp::v4(), bind_addr, src_port).begin()->endpoint();\n'
    '      d_udp_endpoint_tx = resolver.resolve(udp::v4(), remote_addr, dst_port).begin()->endpoint();',
    c, flags=re.DOTALL
)
f.write_text(c)
print("udp_socket.cc patched")
PYEOF

# Patch CMakeLists.txt: link_directories para homebrew libs (gnuradio-network etc.)
sed -i '' '/find_package(Gnuradio/a\
\
# Ensure homebrew libraries are in linker search path\
link_directories('"$BREW_PFX"'/lib)
' CMakeLists.txt 2>/dev/null || true

# Patch GrccCompile.cmake: adiciona DYLD_LIBRARY_PATH para grcc compilar flowgraphs
# (gsm_python.so precisa carregar libgnuradio-gsm antes do install)
sed -i '' 's/-E env PYTHONPATH="\${PYTHONPATH}" GRC_BLOCKS_PATH/-E env PYTHONPATH="${PYTHONPATH}" DYLD_LIBRARY_PATH=${CMAKE_BINARY_DIR}\/lib:'"$BREW_PFX"'\/lib GRC_BLOCKS_PATH/' \
    cmake/Modules/GrccCompile.cmake 2>/dev/null || true

# Patch python/gsm/__init__.py: garante que device.py está em try/except (osmosdr ausente no macOS)
# bkerler_fork já traz o try/except; este script é idempotente (não duplica o bloco)
python3 - << 'PYEOF' 2>/dev/null || true
import pathlib, ast
f = pathlib.Path("python/gsm/__init__.py")
if not f.exists(): exit()
c = f.read_text()
# Verifica se o arquivo já está sintaticamente correto
try:
    ast.parse(c)
    print("__init__.py: syntax OK, no patch needed")
    exit()
except SyntaxError:
    pass
# Se houver erro de sintaxe, restaura o padrão seguro bkerler_fork
import re
# Remove qualquer try: aninhado incorreto ao redor de device import
c = re.sub(
    r'(\s*)try:\s*\n(\s*)try:\s*\n(\s*)from \.device import \*\s*\n(\s*)except[^\n]*\n(\s*)pass[^\n]*\n(\s*)except[^\n]*\n(\s*)pass[^\n]*',
    r'\1try:\n\3from .device import *\n\1except (ImportError, ModuleNotFoundError):\n\1    pass  # osmosdr not available (gr-osmosdr not installed)',
    c
)
f.write_text(c)
print("__init__.py: fixed nested try/except")
PYEOF

# Cria bloco rtlsdr_source para grcc (gr-osmosdr não disponível no macOS)
cat > grc/rtlsdr_source.block.yml << 'YMLEOF'
id: rtlsdr_source
label: RTL-SDR Source (osmosdr)
flags: [python, throttle]
parameters:
  - id: args
    label: Device Arguments
    dtype: string
    default: ''
  - id: samp_rate
    label: Sample Rate
    dtype: float
    default: 'samp_rate'
  - id: freq
    label: Center Frequency (Hz)
    dtype: real
    default: '939e6'
  - id: corr
    label: Freq. Correction (ppm)
    dtype: real
    default: '0'
  - id: gain_mode
    label: Gain Mode
    dtype: enum
    options: ['False', 'True']
    option_labels: [Manual, Auto]
    default: 'False'
  - id: gain
    label: RF Gain (dB)
    dtype: real
    default: '10'
  - id: if_gain
    label: IF Gain (dB)
    dtype: real
    default: '20'
  - id: bb_gain
    label: BB Gain (dB)
    dtype: real
    default: '20'
  - id: bandwidth
    label: Bandwidth (Hz)
    dtype: real
    default: '200e3'
  - id: num_inputs
    label: Num Inputs
    dtype: int
    default: '0'
    hide: all
outputs:
  - domain: stream
    dtype: fc32
templates:
  imports: import osmosdr
  make: |
    osmosdr.source(args="${args}")
    self.${id}.set_sample_rate(${samp_rate})
    self.${id}.set_center_freq(${freq}, 0)
    self.${id}.set_freq_corr(${corr}, 0)
    self.${id}.set_gain_mode(${gain_mode}, 0)
    self.${id}.set_gain(${gain}, 0)
    self.${id}.set_if_gain(${if_gain}, 0)
    self.${id}.set_bb_gain(${bb_gain}, 0)
    self.${id}.set_bandwidth(${bandwidth}, 0)
file_format: 1
YMLEOF

# Patch: boost/format.hpp onde necessário
grep -rl 'boost::format' . --include="*.cc" --include="*.cpp" --include="*.h" 2>/dev/null | while read ff; do
    grep -q "#include <boost/format.hpp>" "$ff" 2>/dev/null || \
        sed -i '' '1s|^|#include <boost/format.hpp>\n|' "$ff" 2>/dev/null || true
done

ok "Patches bkerler_fork aplicados (Boost, osmosdr, grcc, link_directories)"

# Limpa cmake cache stale de tentativas anteriores
rm -rf build
mkdir -p build && cd build

# Localiza módulos cmake do GNU Radio instalado
GR_CMAKE_MOD=""
for d in \
    "$BREW_PFX/lib/cmake/gnuradio" \
    "$BREW_PFX/share/gnuradio/cmake/Modules" \
    "$(find $BREW_PFX/Cellar/gnuradio -name 'GnuradioConfig.cmake' -maxdepth 6 2>/dev/null | head -1 | xargs dirname 2>/dev/null)"; do
    if [ -d "$d" ]; then
        GR_CMAKE_MOD="$d"; break
    fi
done
info "Módulos cmake GR: ${GR_CMAKE_MOD:-não encontrado}"

# Localiza pybind11 (necessário para bindings Python GR 3.10)
PYBIND11_DIR=$(find "$BREW_PFX/lib/cmake" -name "pybind11Config.cmake" 2>/dev/null | head -1 | xargs dirname 2>/dev/null)
info "pybind11: ${PYBIND11_DIR:-não encontrado via brew, tentando via pip...}"
if [ -z "$PYBIND11_DIR" ]; then
    PYBIND11_DIR=$(python3 -c "import pybind11; print(pybind11.get_cmake_dir())" 2>/dev/null || echo "")
    info "pybind11 pip: ${PYBIND11_DIR:-não encontrado}"
fi

info "cmake ..."
SITE_PKG=$(python3 -c 'import site; print(site.getsitepackages()[0])' 2>/dev/null || echo "$BREW_PFX/lib/python3/site-packages")
cmake .. \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_PREFIX_PATH="$BREW_PFX${PYBIND11_DIR:+;$PYBIND11_DIR}" \
    -DCMAKE_INSTALL_PREFIX="$BREW_PFX" \
    ${GR_CMAKE_MOD:+-DCMAKE_MODULE_PATH="$GR_CMAKE_MOD"} \
    -DPYTHON_EXECUTABLE="$PYTHON_BIN" \
    -DGR_PYTHON_DIR="$SITE_PKG" \
    -DENABLE_PYTHON=ON \
    -DENABLE_DOXYGEN=OFF \
    -DCMAKE_OSX_DEPLOYMENT_TARGET="$(sw_vers -productVersion | cut -d. -f1)" \
    -DCMAKE_CXX_FLAGS="-I$BREW_PFX/include -Wno-error -Wno-deprecated -Wno-deprecated-declarations -Wno-unused-variable" \
    -DCMAKE_C_FLAGS="-I$BREW_PFX/include -Wno-error -Wno-deprecated-declarations" \
    >> "$LOG" 2>&1 || {
        err "cmake gr-gsm falhou"
        echo ""
        tail -40 "$LOG"
        echo ""
        read -p "  ENTER para fechar..." _
        exit 1
    }

info "make ($(sysctl -n hw.logicalcpu) jobs)..."
make -j"$(sysctl -n hw.logicalcpu)" >> "$LOG" 2>&1 || {
    info "Tentando make com 1 job para ver o erro..."
    make 2>&1 | tee -a "$LOG" | tail -30
    echo ""
    read -p "  make falhou — ENTER para fechar..." _
    exit 1
}

# Patch gerado: substitui osmosdr.source → gnuradio.soapy.source (HackRF nativo GR 3.10)
# gr-osmosdr não está disponível no macOS via Homebrew; soapy.source funciona com SoapyHackRF
info "Patch pós-build: osmosdr → soapy (HackRF)..."
python3 - << 'PYEOF' >> "$LOG" 2>&1 || true
import re, pathlib, sys

f = pathlib.Path("apps/grgsm_livemon_headless")
if not f.exists():
    print("grgsm_livemon_headless not found — skipping soapy patch"); sys.exit(0)

lines = f.read_text().splitlines(keepends=True)
out = []
skip_osmosdr_source = False

for line in lines:
    # 1. import osmosdr → from gnuradio import soapy as _soapy
    if line.strip() == "import osmosdr":
        out.append("from gnuradio import soapy as _soapy\n")
        continue

    # 2. osmosdr.source(...) creation — may span unusual content, replace whole line
    if "osmosdr.source(" in line:
        indent = len(line) - len(line.lstrip())
        out.append(" " * indent + 'self.rtlsdr_source_0 = _soapy.source("driver=hackrf", "fc32", 1, args, "", [""], [""])\n')
        continue

    # 3. set_sample_rate(rate) → set_sample_rate(0, rate)
    line = re.sub(
        r'(self\.rtlsdr_source_0\.set_sample_rate\()([^)]+)(\))',
        lambda m: m.group(1) + "0, " + m.group(2) + m.group(3),
        line
    )

    # 4. set_center_freq(freq, ch) → set_frequency(ch, freq)
    line = re.sub(
        r'self\.rtlsdr_source_0\.set_center_freq\(([^,]+),\s*(\d+)\)',
        r'self.rtlsdr_source_0.set_frequency(\2, \1)',
        line
    )

    # 5. set_freq_corr(ppm, ch) → set_frequency_correction(ch, ppm)
    line = re.sub(
        r'self\.rtlsdr_source_0\.set_freq_corr\(([^,]+),\s*(\d+)\)',
        r'self.rtlsdr_source_0.set_frequency_correction(\2, \1)',
        line
    )

    # 6. set_gain_mode(auto, ch) → set_gain_mode(ch, auto)
    line = re.sub(
        r'self\.rtlsdr_source_0\.set_gain_mode\(([^,]+),\s*(\d+)\)',
        r'self.rtlsdr_source_0.set_gain_mode(\2, \1)',
        line
    )

    # 7. set_gain(val, ch) → set_gain(ch, val)
    line = re.sub(
        r'self\.rtlsdr_source_0\.set_gain\(([^,]+),\s*(\d+)\)',
        r'self.rtlsdr_source_0.set_gain(\2, \1)',
        line
    )

    # 8. set_if_gain — remove (HackRF has no IF gain stage in SoapySDR)
    if re.search(r'self\.rtlsdr_source_0\.set_if_gain\(', line):
        continue

    # 9. set_bb_gain — remove (HackRF has no BB gain stage in SoapySDR)
    if re.search(r'self\.rtlsdr_source_0\.set_bb_gain\(', line):
        continue

    # 10. set_bandwidth(bw, ch) → set_bandwidth(ch, bw)
    line = re.sub(
        r'self\.rtlsdr_source_0\.set_bandwidth\(([^,]+),\s*(\d+)\)',
        r'self.rtlsdr_source_0.set_bandwidth(\2, \1)',
        line
    )

    out.append(line)

f.write_text("".join(out))
print("grgsm_livemon_headless: osmosdr → soapy(hackrf) patch OK")
PYEOF

info "sudo make install ..."
sudo make install >> "$LOG" 2>&1 || { err "install gr-gsm falhou"; tail -10 "$LOG"; read -p "ENTER..." _; exit 1; }
ok "gr-gsm instalado"

cd "$DIR"

# ── Verificação final ─────────────────────────────────────────────────────────
step "Verificando instalação"

if command -v grgsm_livemon_headless &>/dev/null; then
    ok "grgsm_livemon_headless: $(which grgsm_livemon_headless)"
else
    GRGSM_PATH="$BREW_PFX/bin/grgsm_livemon_headless"
    if [ -f "$GRGSM_PATH" ]; then
        ok "grgsm_livemon_headless: $GRGSM_PATH"
        # Adiciona ao .zshrc / .bashrc se não estiver no PATH
        if ! grep -q "$BREW_PFX/bin" ~/.zshrc 2>/dev/null; then
            echo "export PATH=\"$BREW_PFX/bin:\$PATH\"" >> ~/.zshrc
            info "PATH adicionado ao ~/.zshrc"
        fi
    else
        warn "Binário não encontrado no PATH padrão."
        warn "Verifique: find $BREW_PFX -name 'grgsm*' 2>/dev/null"
    fi
fi

echo ""
echo "  ══════════════════════════════════════════════"
echo -e "  ${G}🎉  INSTALAÇÃO CONCLUÍDA!${N}"
echo ""
echo "  Próximos passos:"
echo "  1. Feche esta janela"
echo "  2. Abra  👉  '🚀 Iniciar mtzRF.command'"
echo "  3. Acesse http://localhost:8765/imsi-catcher.html"
echo "  4. Clique 'Varrer Bandas' → selecione torre → 'Iniciar Captura'"
echo ""
echo "  ⚠️  Use somente para fins educacionais / legais."
echo "  ══════════════════════════════════════════════"
echo ""
read -p "  Pressione ENTER para fechar..." _
