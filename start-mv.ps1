param([switch]$StartEngines)
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
. (Join-Path $taskRoot 'tools/mv_listener_guard.ps1')
$taskPython = Join-Path $taskRoot '.mv-venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Run tools/setup_mv.ps1 first.' }
foreach ($taskDirectory in @('input/mv','output/mv','temp','.cache','.runtime')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $taskRoot $taskDirectory) | Out-Null
}
$env:PYTHONNOUSERSITE = '1'
$env:HF_HOME = Join-Path $taskRoot '.cache/huggingface'
$env:TORCH_HOME = Join-Path $taskRoot '.cache/torch'
$env:XDG_CACHE_HOME = Join-Path $taskRoot '.cache'
$env:TEMP = Join-Path $taskRoot 'temp'
$env:TMP = $env:TEMP
$env:CUDA_VISIBLE_DEVICES = ''
$env:OMP_NUM_THREADS = '2'
$env:MKL_NUM_THREADS = '2'
$env:PYTHONIOENCODING = 'utf-8'
$env:HF_HUB_DISABLE_TELEMETRY = '1'
$env:OPENVINO_TELEMETRY_OPT_OUT = '1'
if ($StartEngines) {
    $taskShell = (Get-Process -Id $PID).Path
    $taskStarted = @{}
    foreach ($taskService in @(
        @{port=8794; script='start-comfy.ps1'; probe='http://127.0.0.1:8794/object_info/VoiceMotionMVCreate'; name='comfy'},
        @{port=8795; script='start-mv-proxy.ps1'; probe='http://127.0.0.1:8795/api/v2/health'; name='proxy'})) {
        $taskListener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback,$taskService.port)
        $taskPortFree = $false
        try { $taskListener.Start(); $taskPortFree=$true } catch {} finally { $taskListener.Stop() }
        if ($taskPortFree) {
            $taskProcess = Start-Process -FilePath $taskShell -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',(Join-Path $taskRoot $taskService.script)) -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskRoot ('.runtime/mv-'+$taskService.name+'.stdout.log')) -RedirectStandardError (Join-Path $taskRoot ('.runtime/mv-'+$taskService.name+'.stderr.log')) -PassThru
            $taskStarted[$taskService.name] = @{pid=$taskProcess.Id; script=(Join-Path $taskRoot $taskService.script); created=$taskProcess.StartTime.ToUniversalTime().ToString('o')}
            $taskExpectedLauncherId=$taskProcess.Id
            $taskExpectedCreated=$taskProcess.StartTime.ToUniversalTime().ToString('o')
        } else {
            $taskOwner=Get-MVListenerOwnership -Root $taskRoot -Service $taskService.name
            if (-not $taskOwner) { throw ('Port '+$taskService.port+' has no verifiable project listener. Refusing to attach or stop it.') }
            $taskExpectedLauncherId=$taskOwner.launcher_id
            $taskExpectedCreated=$taskOwner.created
        }
        $taskReady = $false
        foreach ($taskAttempt in 1..60) {
            # Applies to newly launched listeners too, and is deliberately outside
            # the retry catch: an unknown owner must never receive a health probe.
            $taskOwner=Get-MVListenerOwnership -Root $taskRoot -Service $taskService.name -ExpectedLauncherId $taskExpectedLauncherId -ExpectedCreated $taskExpectedCreated
            if (-not $taskOwner) { Start-Sleep -Milliseconds 500; continue }
            try {
                $taskReply=Invoke-RestMethod -Uri $taskService.probe -NoProxy -TimeoutSec 2
                if ($taskService.name -eq 'comfy' -and -not $taskReply.VoiceMotionMVCreate) { throw 'MV node missing' }
                $taskReady=$true; break
            } catch { Start-Sleep -Milliseconds 500 }
        }
        if (-not $taskReady) { throw ('Project '+$taskService.name+' is not ready. Inspect only this project .runtime logs.') }
    }
    if ($taskStarted.Count) { $taskStarted | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $taskRoot '.runtime/mv-started-engines.json') -Encoding utf8 }
}
Push-Location $taskRoot
try { & $taskPython (Join-Path $taskRoot 'mv_server.py') --port 8797 } finally { Pop-Location }
