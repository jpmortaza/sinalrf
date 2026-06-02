#!/bin/bash
# ─────────────────────────────────────────────────────
#  SINALRF — Launcher
#  Duplo clique no Finder para iniciar o servidor.
# ─────────────────────────────────────────────────────

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# Garante que Homebrew e ferramentas locais estão no PATH
# (necessário quando abre via duplo clique no Finder)
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:$PATH"

# gr-gsm instalado via conda (se existir)
for CONDA_ENV in "$HOME/miniforge3/envs/sinalrf-gsm/bin" "$HOME/miniconda3/envs/sinalrf-gsm/bin" "$HOME/opt/anaconda3/envs/sinalrf-gsm/bin"; do
    [ -d "$CONDA_ENV" ] && export PATH="$CONDA_ENV:$PATH" && break
done

clear

echo ""
echo "  ██████████████████████████████████████████████"
echo "  ██                                          ██"
echo "  ██   📡  S I N A L R F  v2.0               ██"
echo "  ██   Plataforma RF + HackRF + Áudio         ██"
echo "  ██                                          ██"
echo "  ██████████████████████████████████████████████"
echo ""
echo "  🌐  Dashboard:   http://localhost:8765"
echo "  📻  Rádio FM:    http://localhost:8765/radio.html"
echo "  🌌  Scanner:     http://localhost:8765/scanner.html"
echo "  🔮  3D RF:       http://localhost:8765/3d.html"
echo "  ❤️   Saúde:       http://localhost:8765/health.html"
echo "  📱  IMSI:        http://localhost:8765/imsi-catcher.html"
echo ""
echo "  Para parar: Ctrl + C"
echo ""
echo "──────────────────────────────────────────────────"
echo ""

# Verifica se HackRF está conectado
if command -v hackrf_info &>/dev/null; then
    if hackrf_info 2>&1 | grep -q "Serial number\|Found HackRF"; then
        echo "  ✅  HackRF One detectado"
    else
        echo "  ⚠️   HackRF não encontrado (modo simulação)"
    fi
else
    echo "  ⚠️   hackrf_info não encontrado (brew install hackrf)"
fi

echo ""

# Instala/sincroniza dependências sempre
echo "  📦  Verificando dependências..."
if command -v uv &>/dev/null; then
    uv venv --quiet 2>/dev/null || true
    uv pip install -r requirements.txt --quiet
else
    if [ ! -d ".venv" ]; then
        python3 -m venv .venv
    fi
    .venv/bin/pip install -r requirements.txt --quiet
fi
echo "  ✅  Dependências OK"
echo ""

# Inicia o servidor
echo "  🚀  Iniciando servidor..."
echo ""

if command -v uv &>/dev/null; then
    uv run python server.py
elif [ -f ".venv/bin/python" ]; then
    .venv/bin/python server.py
else
    python3 server.py
fi
