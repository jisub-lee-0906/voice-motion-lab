$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskPython = Join-Path $taskRoot '.comfy-venv/Scripts/python.exe'
$taskSource = Join-Path $taskRoot '.runtime/ComfyUI/main.py'
if (-not (Test-Path -LiteralPath $taskPython) -or -not (Test-Path -LiteralPath $taskSource)) {
    throw 'Install the independent CPU Comfy runtime with tools/setup_comfy.ps1 first.'
}
foreach ($taskDirectory in @('input','output','user','models','temp/comfy','.runtime/comfy-state/custom_nodes','.cache/huggingface','.cache/torch')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $taskRoot $taskDirectory) | Out-Null
}
$env:TEMP = Join-Path $taskRoot 'temp'
$env:TMP = $env:TEMP
$env:PYTHONNOUSERSITE = '1'
$env:HF_HOME = Join-Path $taskRoot '.cache/huggingface'
$env:TORCH_HOME = Join-Path $taskRoot '.cache/torch'
$env:XDG_CACHE_HOME = Join-Path $taskRoot '.cache'
$env:HF_HUB_OFFLINE = '1'
$env:VOICE_MOTION_MODEL = Join-Path $taskRoot '.runtime/models/basic_pitch.onnx'
$env:OMP_NUM_THREADS = '2'
$env:MKL_NUM_THREADS = '2'
$env:CUDA_VISIBLE_DEVICES = ''
Push-Location $taskRoot
try {
    & $taskPython $taskSource --cpu --listen 127.0.0.1 --port 8794 `
        --base-directory (Join-Path $taskRoot '.runtime/comfy-state') `
        --input-directory (Join-Path $taskRoot 'input') `
        --output-directory (Join-Path $taskRoot 'output') `
        --temp-directory (Join-Path $taskRoot 'temp/comfy') `
        --user-directory (Join-Path $taskRoot 'user') `
        --models-directory (Join-Path $taskRoot 'models') `
        --extra-model-paths-config (Join-Path $taskRoot 'comfy_paths.yaml') `
        --disable-all-custom-nodes --whitelist-custom-nodes voice_motion `
        --offline --disable-auto-launch --disable-assets --disable-pinned-memory --disable-comfy-compiler
} finally { Pop-Location }
