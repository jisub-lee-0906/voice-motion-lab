param([switch]$WithComfy)
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskPython = Join-Path $taskRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Run setup.ps1 to create this project virtual environment first.' }
$env:PYTHONNOUSERSITE = '1'
$env:TEMP = Join-Path $taskRoot 'temp'
$env:TMP = $env:TEMP
$env:OMP_NUM_THREADS = '2'
$env:MKL_NUM_THREADS = '2'
if ($WithComfy) { $env:VOICE_MOTION_COMFY_URL = 'http://127.0.0.1:8794' }
Push-Location $taskRoot
try { & $taskPython server.py --port 8793 } finally { Pop-Location }
