param([string]$Python='python')
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $PSScriptRoot
$taskEnv=Join-Path $taskRoot '.mv-venv'
$taskPython=Join-Path $taskEnv 'Scripts/python.exe'
New-Item -ItemType Directory -Force -Path (Join-Path $taskRoot 'temp') | Out-Null
$env:PYTHONNOUSERSITE='1'
$env:PIP_CACHE_DIR=Join-Path $taskRoot '.cache/pip'
$env:TEMP=Join-Path $taskRoot 'temp'; $env:TMP=$env:TEMP
if (-not (Test-Path -LiteralPath $taskPython)) {
    & $Python -m venv $taskEnv
    if ($LASTEXITCODE -ne 0) { throw 'Project MV environment creation failed.' }
}
& $taskPython -m pip install -r (Join-Path $PSScriptRoot 'mv-requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Project MV packages failed.' }
if (-not (Test-Path -LiteralPath (Join-Path $taskRoot '.comfy-venv/Scripts/python.exe'))) {
    & (Join-Path $PSScriptRoot 'setup_comfy.ps1') -Python $Python
}
$taskComfyPython=Join-Path $taskRoot '.comfy-venv/Scripts/python.exe'
& $taskComfyPython -m pip install 'transformers==5.20.0' 'soynlp==0.0.493'
if ($LASTEXITCODE -ne 0) { throw 'Korean alignment dependency failed.' }
& $taskPython (Join-Path $PSScriptRoot 'setup_mv_models.py')
if ($LASTEXITCODE -ne 0) { throw 'Pinned ASR model preparation failed.' }
Write-Host 'Ready. Run ./start-mv.ps1 -StartEngines, then open http://127.0.0.1:8797/'
