# mtzRF - orquestrador do sistema embarcado (ASCII puro p/ PowerShell 5.1).
# Sobe o servidor (backend oculto), abre em TELA CHEIA / KIOSK e, ao fechar a
# janela (Alt+F4), encerra o servidor automaticamente.

Set-Location -Path $PSScriptRoot
$url = "http://127.0.0.1:8765/"
$log = Join-Path $PSScriptRoot "abrir-app.log"
function L($m){ "$(Get-Date -Format HH:mm:ss) $m" | Out-File -FilePath $log -Append -Encoding utf8 }
"" | Out-File $log -Encoding utf8
L "inicio"

$env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"
$hk = "C:\Dev\hackrf\sdr-tools\miniforge3\Library\bin"
if ((Test-Path "$hk\hackrf_info.exe") -and ($env:PATH -notlike "*$hk*")) { $env:PATH = "$hk;$env:PATH" }

# 1) servidor (oculto)
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
try {
    $srv = Start-Process $py -ArgumentList "server.py" -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput "srv_out.log" -RedirectStandardError "srv_err.log"
    L "servidor pid=$($srv.Id)"
} catch { L "ERRO servidor: $_" }

function Stop-Servidor {
    L "encerrando servidor"
    if ($srv -and -not $srv.HasExited) { Stop-Process -Id $srv.Id -Force -ErrorAction SilentlyContinue }
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like '*server.py*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    foreach ($n in 'hackrf_transfer','hackrf_sweep') {
        Start-Process taskkill -ArgumentList "/F","/IM","$n.exe","/T" -WindowStyle Hidden -ErrorAction SilentlyContinue
    }
}

# 2) espera o servidor responder
$up = $false
for ($i = 0; $i -lt 60; $i++) {
    try { if ((Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200) { $up = $true; break } } catch {}
    Start-Sleep -Seconds 2
}
L "up=$up"
if (-not $up) { Stop-Servidor; exit 1 }

# 3) navegador + perfil limpo (evita travas de instancias anteriores)
$dataDir = Join-Path $env:LOCALAPPDATA "mtzRF\app"
Remove-Item $dataDir -Recurse -Force -ErrorAction SilentlyContinue
$cands = @(
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe"
)
$nav = $cands | Where-Object { Test-Path $_ } | Select-Object -First 1
L "nav=$nav"
if (-not $nav) { Start-Process $url; exit 0 }

$argl = @("--kiosk", $url, "--edge-kiosk-type=fullscreen", "--no-first-run",
          "--no-default-browser-check", "--user-data-dir=$dataDir")
try { Start-Process $nav -ArgumentList $argl; L "edge lancado" } catch { L "ERRO edge: $_" }
Start-Sleep -Seconds 3
L "msedge=$((Get-Process msedge -EA SilentlyContinue | Measure-Object).Count)"

# 4) espera a janela aparecer
$apareceu = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 2
    if (Get-Process msedge,chrome -EA SilentlyContinue | Where-Object { $_.MainWindowTitle -like "*mtzRF*" }) { $apareceu = $true; break }
}
L "apareceu=$apareceu"

# 5) enquanto a janela existir, segura; ao fechar, encerra o servidor
if ($apareceu) {
    $ausente = 0
    while ($true) {
        Start-Sleep -Seconds 2
        $viva = Get-Process msedge,chrome -EA SilentlyContinue | Where-Object { $_.MainWindowTitle -like "*mtzRF*" }
        if ($viva) { $ausente = 0 } else { $ausente++; if ($ausente -ge 2) { break } }
    }
    L "janela fechada"
    Stop-Servidor
} else {
    L "janela nao detectada - servidor segue rodando"
}
L "fim"
