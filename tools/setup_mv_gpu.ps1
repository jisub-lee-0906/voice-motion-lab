param([switch]$Install,[switch]$ResourceApproved,[string]$Python='python')
$ErrorActionPreference='Stop'
$qualityRoot=Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'mv_gpu_guard.ps1')
$qualityEnvironment=Join-Path $qualityRoot '.gpu-venv'
$qualityPython=Join-Path $qualityEnvironment 'Scripts/python.exe'
$qualitySource=Join-Path $qualityRoot '.runtime/ComfyUI'
$qualityRevision='bdc63e72f5ab29fde448c161db3057827148c0ef'
if (-not $Install) {
    [pscustomobject]@{phase='prepared; not GPU-tested';environment=$qualityEnvironment;
        torch='2.14.1+cu130';torchvision='0.29.1+cu130';python='3.12 x64';
        action='Use -Install -ResourceApproved only after a fresh explicit resource approval. No installation or probe performed.'} | ConvertTo-Json
    return
}
$qualityResources=Assert-MVQualityResourceWindow -ResourceApproved:$ResourceApproved
if (-not (Test-Path -LiteralPath (Join-Path $qualitySource 'main.py')) -or
    (git -C $qualitySource rev-parse HEAD).Trim() -ne $qualityRevision) {
    throw 'Prepare the separate pinned Comfy source first; never update an existing runtime implicitly.'
}
if ([IO.Path]::GetFullPath($qualityEnvironment) -ne $qualityEnvironment -or
    ((Test-Path -LiteralPath $qualityEnvironment) -and (Get-Item -LiteralPath $qualityEnvironment).LinkType)) {
    throw 'The GPU environment must be this project non-linked .gpu-venv.'
}
$qualityVersion=& $Python -c 'import sys;print(str(sys.version_info.major)+"."+str(sys.version_info.minor))'
if ($LASTEXITCODE -ne 0 -or $qualityVersion.Trim() -ne '3.12') { throw 'Use a Python 3.12 x64 interpreter; do not alter system Python.' }
foreach ($qualityDirectory in @('temp/gpu','.cache/mv-gpu/pip','.runtime/mv-quality')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $qualityRoot $qualityDirectory) | Out-Null
}
$env:PYTHONNOUSERSITE='1';$env:PYTHONDONTWRITEBYTECODE='1'
$env:PIP_CACHE_DIR=Join-Path $qualityRoot '.cache/mv-gpu/pip'
$env:TEMP=Join-Path $qualityRoot 'temp/gpu';$env:TMP=$env:TEMP
if (-not (Test-Path -LiteralPath $qualityPython)) {
    & $Python -m venv $qualityEnvironment
    if ($LASTEXITCODE -ne 0) { throw 'Project-only GPU environment creation failed.' }
}
# URLs and hashes were read from the official cu130 registry. No CPU wheel swap.
$qualityTorch='https://download-r2.pytorch.org/whl/cu130/torch-2.14.1%2Bcu130-cp312-cp312-win_amd64.whl#sha256=0a09031e93632d14ef49553acd6211ece3ae9cdc0969a369b8c24e3cc4d15be9'
$qualityVision='https://download-r2.pytorch.org/whl/cu130/torchvision-0.29.1%2Bcu130-cp312-cp312-win_amd64.whl#sha256=14d3195e5e657fef46d08de0d3470da03a983fa383d8aa279e36e47e95d991c6'
& $qualityPython -m pip install $qualityTorch $qualityVision --index-url 'https://download.pytorch.org/whl/cu130'
if ($LASTEXITCODE -ne 0) { throw 'Official CUDA wheel installation failed; no other environment was changed.' }
& $qualityPython -m pip install -r (Join-Path $qualitySource 'requirements.txt') -c (Join-Path $PSScriptRoot 'mv_gpu_constraints.txt')
if ($LASTEXITCODE -ne 0) { throw 'Pinned independent GPU Comfy dependencies failed; do not change the CPU environment.' }
& $qualityPython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'GPU dependency compatibility check failed.' }
$qualityResources | ConvertTo-Json | Set-Content (Join-Path $qualityRoot '.runtime/mv-quality/setup-resource-snapshot.json') -Encoding utf8
Write-Output 'Environment installed only. GPU inference remains untested. Download pinned assets separately, then obtain a fresh resource approval before start-mv-gpu.ps1.'
