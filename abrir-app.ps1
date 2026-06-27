# mtzRF — abre a interface como aplicativo dedicado (modo "embarcado").
# Espera o servidor subir e abre o Edge/Chrome em modo --app (sem abas/barra de
# endereço), numa janela isolada. Não é o navegador comum.

$ErrorActionPreference = "SilentlyContinue"
$url = "http://localhost:8765/"

# 1) aguarda o servidor responder (até ~120s)
for ($i = 0; $i -lt 60; $i++) {
    try {
        if ((Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200) { break }
    } catch { }
    Start-Sleep -Seconds 2
}

# 2) localiza Edge ou Chrome
$dataDir = Join-Path $env:LOCALAPPDATA "mtzRF\app"
$candidatos = @(
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe"
)
$navegador = $candidatos | Where-Object { Test-Path $_ } | Select-Object -First 1

# 3) abre em modo app (janela dedicada, sem abas/URL) ou cai no navegador padrão
if ($navegador) {
    Start-Process $navegador -ArgumentList @(
        "--app=$url",
        "--start-maximized",
        "--no-first-run",
        "--no-default-browser-check",
        "--user-data-dir=$dataDir"
    )
} else {
    Start-Process $url
}
