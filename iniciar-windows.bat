@echo off
REM ---------------------------------------------------------------
REM  MTZRF - Launcher para Windows
REM  Duplo clique para iniciar o servidor.
REM ---------------------------------------------------------------
setlocal
chcp 65001 >nul
cd /d "%~dp0"

REM Garante UTF-8 para os emojis do console nao quebrarem
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

REM Coloca as ferramentas do HackRF (hackrf_info/sweep/transfer) no PATH.
REM Instaladas via conda-forge (Miniforge) em sdr-tools\miniforge3\Library\bin.
set "HACKRF_BIN=C:\Dev\hackrf\sdr-tools\miniforge3\Library\bin"
if exist "%HACKRF_BIN%\hackrf_info.exe" set "PATH=%HACKRF_BIN%;%PATH%"

echo.
echo   ==============================================
echo      MTZRF - Plataforma RF + HackRF + Audio
echo   ==============================================
echo.
echo   Dashboard:  http://localhost:8765
echo   Radio FM:   http://localhost:8765/radio.html
echo   Scanner:    http://localhost:8765/scanner.html
echo   Analista:   http://localhost:8765/analista.html
echo   3D RF:      http://localhost:8765/3d.html
echo   Saude:      http://localhost:8765/health.html
echo   IMSI:       http://localhost:8765/intercept.html
echo.
echo   Para parar: Ctrl + C
echo   ----------------------------------------------
echo.

REM Cria o ambiente virtual na primeira execucao
if not exist ".venv\Scripts\python.exe" (
    echo   [setup] Criando ambiente virtual...
    python -m venv .venv
)

echo   [setup] Instalando/atualizando dependencias...
".venv\Scripts\python.exe" -m pip install --upgrade pip -q
".venv\Scripts\python.exe" -m pip install -r requirements.txt -q

REM Detecta HackRF (opcional)
where hackrf_info >nul 2>&1 && (
    echo   [ok] hackrf_info encontrado
) || (
    echo   [aviso] HackRF nao encontrado - rodando em modo simulacao
)

echo.
echo   [run] Iniciando mtzRF em tela cheia (kiosk)...
echo   Para sair: Alt + F4 na janela (encerra o servidor automaticamente).
echo.

REM Orquestrador: sobe o servidor (backend oculto), abre em kiosk e, ao fechar
REM a janela, encerra o servidor. Este console fica minimizado durante o uso.
powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0abrir-app.ps1"

endlocal
