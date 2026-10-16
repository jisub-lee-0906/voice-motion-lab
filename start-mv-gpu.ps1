param([switch]$ResourceApproved)
$ErrorActionPreference='Stop'
$qualityRoot=$PSScriptRoot
. (Join-Path $qualityRoot 'tools/mv_gpu_guard.ps1')
$qualityResources=Assert-MVQualityResourceWindow -ResourceApproved:$ResourceApproved
$qualityPython=Join-Path $qualityRoot '.gpu-venv/Scripts/python.exe'
$qualitySource=Join-Path $qualityRoot '.runtime/ComfyUI/main.py'
if (-not (Test-Path -LiteralPath $qualityPython) -or -not (Test-Path -LiteralPath $qualitySource)) { throw 'Prepare the dedicated GPU environment first.' }
if ((git -C (Split-Path -Parent $qualitySource) rev-parse HEAD).Trim() -ne 'bdc63e72f5ab29fde448c161db3057827148c0ef') { throw 'Unexpected project Comfy source revision.' }
if (@(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object LocalPort -eq 8796).Count) { throw 'Port 8796 is occupied. Refusing to attach or stop its owner.' }
& $qualityPython (Join-Path $qualityRoot 'tools/setup_mv_quality.py') --verify
if ($LASTEXITCODE -ne 0) { throw 'Pinned quality assets are missing or changed.' }
foreach ($qualityDirectory in @('.runtime/mv-quality/comfy-state','input/mv-quality','output/mv-quality','temp/gpu','user/gpu','.cache/mv-gpu/huggingface','.cache/mv-gpu/torch','.cache/mv-gpu/inductor','.cache/mv-gpu/cuda')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $qualityRoot $qualityDirectory) | Out-Null
}
$env:PYTHONNOUSERSITE='1';$env:PYTHONDONTWRITEBYTECODE='1';$env:PYTHONIOENCODING='utf-8'
$env:HF_HOME=Join-Path $qualityRoot '.cache/mv-gpu/huggingface'
$env:TORCH_HOME=Join-Path $qualityRoot '.cache/mv-gpu/torch'
$env:XDG_CACHE_HOME=Join-Path $qualityRoot '.cache/mv-gpu'
$env:TORCHINDUCTOR_CACHE_DIR=Join-Path $qualityRoot '.cache/mv-gpu/inductor'
$env:CUDA_CACHE_PATH=Join-Path $qualityRoot '.cache/mv-gpu/cuda'
$env:TEMP=Join-Path $qualityRoot 'temp/gpu';$env:TMP=$env:TEMP
$env:HF_HUB_OFFLINE='1';$env:TRANSFORMERS_OFFLINE='1';$env:HF_HUB_DISABLE_TELEMETRY='1'
$env:OPENVINO_TELEMETRY_OPT_OUT='1';$env:CUDA_VISIBLE_DEVICES='0'
$env:OMP_NUM_THREADS='2';$env:MKL_NUM_THREADS='2'
# This starts only a new foreground project instance. No kill/unload/restart APIs.
Push-Location $qualityRoot
try {
    & $qualityPython $qualitySource --listen 127.0.0.1 --port 8796 --cuda-device 0 `
        --base-directory (Join-Path $qualityRoot '.runtime/mv-quality/comfy-state') `
        --models-directory (Join-Path $qualityRoot '.runtime/mv-quality/models') `
        --input-directory (Join-Path $qualityRoot 'input/mv-quality') `
        --output-directory (Join-Path $qualityRoot 'output/mv-quality') `
        --temp-directory (Join-Path $qualityRoot 'temp/gpu') `
        --user-directory (Join-Path $qualityRoot 'user/gpu') `
        --disable-all-custom-nodes --offline --disable-auto-launch --disable-assets `
        --disable-comfy-compiler --cache-none --reserve-vram 2 --use-pytorch-cross-attention
    if ($LASTEXITCODE -ne 0) { throw 'The separate GPU Comfy instance exited with an error.' }
} finally { Pop-Location }
