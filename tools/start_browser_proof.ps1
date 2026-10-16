$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskPython = Join-Path $taskRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Run setup.ps1 first.' }
$env:PYTHONNOUSERSITE = '1'
$env:TEMP = Join-Path $taskRoot 'temp'
$env:TMP = $env:TEMP
Write-Output 'Technical WASM proof only: http://127.0.0.1:8796/browser-proof/index.html'
& $taskPython -m http.server 8796 --bind 127.0.0.1 --directory (Join-Path $taskRoot 'web')
