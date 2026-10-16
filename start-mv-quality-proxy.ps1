param([switch]$ResourceApproved)
$ErrorActionPreference='Stop'
$qualityRoot=$PSScriptRoot
if (-not $ResourceApproved) {throw 'Obtain explicit approval for the quality benchmark resource window first.'}
. (Join-Path $qualityRoot 'tools/mv_gpu_guard.ps1')
Assert-MVQualityListener $qualityRoot | Out-Null
if (@(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object LocalPort -eq 8798).Count) {throw 'Port 8798 is occupied. No process will be attached or stopped.'}
$qualityPython=Join-Path $qualityRoot '.mv-venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $qualityPython)) {throw 'The project-only official API proxy package environment is missing.'}
foreach ($qualityDirectory in @('.runtime/mv-quality/proxy-state','temp/gpu','.cache/mv-gpu')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $qualityRoot $qualityDirectory) | Out-Null
}
$env:PYTHONNOUSERSITE='1';$env:PYTHONDONTWRITEBYTECODE='1'
$env:TEMP=Join-Path $qualityRoot 'temp/gpu';$env:TMP=$env:TEMP
$env:XDG_CACHE_HOME=Join-Path $qualityRoot '.cache/mv-gpu'
Remove-Item Env:COMFY_API_KEY -ErrorAction SilentlyContinue
Push-Location $qualityRoot
try {
    & $qualityPython -m comfy_api_proxy.cli run --comfyui 'http://127.0.0.1:8796' `
        --host '127.0.0.1' --port 8798 --max-upload-mb 16 `
        --state-dir (Join-Path $qualityRoot '.runtime/mv-quality/proxy-state')
    if ($LASTEXITCODE -ne 0) {throw 'The dedicated quality API proxy exited with an error.'}
} finally {Pop-Location}
