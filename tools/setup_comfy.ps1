param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskSource = Join-Path $taskRoot '.runtime/ComfyUI'
$taskEnvironment = Join-Path $taskRoot '.comfy-venv'
$taskPython = Join-Path $taskEnvironment 'Scripts/python.exe'
$taskRevision = 'bdc63e72f5ab29fde448c161db3057827148c0ef'
New-Item -ItemType Directory -Force -Path (Join-Path $taskRoot 'temp') | Out-Null
$env:TEMP = Join-Path $taskRoot 'temp'
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = Join-Path $taskRoot '.cache/pip'
$env:PYTHONNOUSERSITE = '1'
if (-not (Test-Path -LiteralPath $taskSource)) {
    git clone --no-checkout https://github.com/Comfy-Org/ComfyUI.git $taskSource
    if ($LASTEXITCODE -ne 0) { throw 'Independent Comfy source download failed.' }
    git -C $taskSource checkout --detach $taskRevision
    if ($LASTEXITCODE -ne 0) { throw 'Cannot check out the tested Comfy revision.' }
} elseif ((git -C $taskSource rev-parse HEAD).Trim() -ne $taskRevision) {
    throw 'An existing independent runtime has another revision. Refusing to reset it.'
}
if (-not (Test-Path -LiteralPath $taskPython)) {
    & $Python -m venv $taskEnvironment
    if ($LASTEXITCODE -ne 0) { throw 'Independent Comfy environment creation failed.' }
}
& $taskPython -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
if ($LASTEXITCODE -ne 0) { throw 'CPU-only PyTorch installation failed.' }
& $taskPython -m pip install -r (Join-Path $taskSource 'requirements.txt') numpy onnxruntime
if ($LASTEXITCODE -ne 0) { throw 'Independent Comfy dependencies failed.' }
Write-Host 'Ready. Start only this project runtime with ./start-comfy.ps1.'
