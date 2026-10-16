param([string]$Python = 'python', [switch]$WithModel)
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskPython = Join-Path $taskRoot '.venv/Scripts/python.exe'
foreach ($taskDirectory in @('temp','.cache/pip','.runtime/models','input','output','user')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $taskRoot $taskDirectory) | Out-Null
}
$env:TEMP = Join-Path $taskRoot 'temp'
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = Join-Path $taskRoot '.cache/pip'
$env:PYTHONNOUSERSITE = '1'
if (-not (Test-Path -LiteralPath $taskPython)) {
    & $Python -m venv (Join-Path $taskRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the project virtual environment.' }
}
& $taskPython -m pip install -r (Join-Path $taskRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Project dependency installation failed.' }
if ($WithModel) {
    & $taskPython (Join-Path $taskRoot 'tools/setup_model.py')
    if ($LASTEXITCODE -ne 0) { throw 'Pinned model installation failed.' }
}
Write-Host 'Ready. Run ./start.ps1 and open http://127.0.0.1:8793'
