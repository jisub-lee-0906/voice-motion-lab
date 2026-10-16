$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
. (Join-Path $taskRoot 'tools/mv_listener_guard.ps1')
$taskPython = Join-Path $taskRoot '.mv-venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    throw 'Create this project .mv-venv and run tools/setup_mv_sdk.py --install there first.'
}
$taskComfyOwner=Get-MVListenerOwnership -Root $taskRoot -Service 'comfy'
if (-not $taskComfyOwner) { throw 'Cannot verify this project Comfy listener on 8794.' }
$taskNode = Invoke-RestMethod -Uri 'http://127.0.0.1:8794/object_info/VoiceMotionMVCreate' -NoProxy -TimeoutSec 5
if (-not $taskNode.VoiceMotionMVCreate) {
    throw 'The isolated Comfy on 8794 must load VoiceMotionMVCreate. Never use the existing Studio.'
}
foreach ($taskDirectory in @('temp','.cache/pip','.runtime/mv-proxy-state')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $taskRoot $taskDirectory) | Out-Null
}
$env:PYTHONNOUSERSITE = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:TEMP = Join-Path $taskRoot 'temp'
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = Join-Path $taskRoot '.cache/pip'
$env:COMFY_BASE_URL = 'http://127.0.0.1:8795'
$env:COMFY_ROUTER_BASE_URL = 'http://127.0.0.1:8795'
Remove-Item Env:COMFY_API_KEY -ErrorAction SilentlyContinue
Push-Location $taskRoot
try {
    # Foreground run avoids the proxy's global background-service state.
    # No --comfyui-base-dir: model-file placement remains disabled.
    & $taskPython -m comfy_api_proxy.cli run `
        --comfyui 'http://127.0.0.1:8794' --host '127.0.0.1' --port 8795 `
        --max-upload-mb 24 --state-dir (Join-Path $taskRoot '.runtime/mv-proxy-state')
    if ($LASTEXITCODE -ne 0) { throw 'The isolated MV API proxy stopped with an error.' }
} finally { Pop-Location }
