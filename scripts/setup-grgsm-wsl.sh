#!/usr/bin/env bash
# mtzRF - instala hackrf + gnuradio + gr-osmosdr + gr-gsm (fork velichkov) no WSL2/Ubuntu.
# Uso (dentro do WSL): ./scripts/setup-grgsm-wsl.sh
# Pesquisa GSM AUTORIZADA apenas (seu proprio SIM / lab / autorizacao escrita).
set -e

echo "[mtzRF] Atualizando apt..."
sudo apt-get update -y

echo "[mtzRF] Instalando HackRF + GNU Radio + gr-osmosdr..."
sudo apt-get install -y \
  git cmake autoconf libtool pkg-config build-essential \
  hackrf libhackrf-dev \
  gnuradio gnuradio-dev gr-osmosdr \
  libosmocore-dev liblog4cpp5-dev libcppunit-dev swig doxygen \
  python3-scipy python3-numpy python3-pip \
  tshark kalibrate-hackrf || true

echo "[mtzRF] Confirma HackRF (precisa do 'usbipd attach' no Windows antes):"
hackrf_info || echo "  (HackRF nao visto - rode 'usbipd attach --wsl --busid X-Y' no Windows)"

# gr-gsm: usa o fork mantido (compativel com GNU Radio 3.10)
if command -v grgsm_livemon_headless >/dev/null 2>&1; then
  echo "[mtzRF] gr-gsm ja instalado."
else
  echo "[mtzRF] Compilando gr-gsm (velichkov/gr-gsm)..."
  SRC="$HOME/src"; mkdir -p "$SRC"; cd "$SRC"
  [ -d gr-gsm ] || git clone https://github.com/velichkov/gr-gsm.git
  cd gr-gsm
  mkdir -p build && cd build
  cmake .. -DCMAKE_BUILD_TYPE=Release
  make -j"$(nproc)"
  sudo make install
  sudo ldconfig
fi

echo ""
echo "[mtzRF] OK. Teste:"
echo "  grgsm_livemon_headless --help"
echo "  kal -s GSM900 -g 40                      # acha celulas GSM ativas"
echo "  grgsm_livemon_headless -f <freq>M -g 40 & # decodifica e envia GSMTAP p/ 4729"
echo "  sudo tshark -i lo -f 'udp port 4729' -Y 'gsm_a.imsi || gsm_a.tmsi'"
echo ""
echo "LEMBRETE LEGAL: somente seu proprio SIM / lab / autorizacao escrita."
