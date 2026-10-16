[CmdletBinding(SupportsShouldProcess)]
param([switch]$IncludeComfy)
# Usage: ./stop-mv.ps1 -WhatIf, then ./stop-mv.ps1; add -IncludeComfy only
# to stop this checkout's isolated renderer too. A manually invoked foreground
# app without a tracked -File launcher must be finished and closed with Ctrl+C.
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\','/')
. (Join-Path $taskRoot 'tools/mv_listener_guard.ps1')
$taskCandidates = [System.Collections.Generic.List[object]]::new()

function Add-MVTrackedLauncher($Service, $Identifier, $Script, $Created, $ShouldStop) {
    if (-not $Identifier) { return }
    $taskNumber = 0
    if (-not [int]::TryParse([string]$Identifier, [ref]$taskNumber) -or $taskNumber -le 0) {
        throw 'Invalid recorded PID. No processes stopped.'
    }
    $taskCandidates.Add([pscustomobject]@{Service=$Service; Identifier=$taskNumber;
        Script=(Join-Path $taskRoot $Script); Created=$Created; ShouldStop=$ShouldStop})
}

function Test-MVArgument([string]$Command, [string]$Argument) {
    if ($Argument -match '^[A-Za-z]:[\\/]') {
        $Command = $Command.Replace('/','\')
        $Argument = $Argument.Replace('/','\')
    }
    $taskEscaped = [regex]::Escape($Argument)
    return $Command -match ('(?i)(?:^|\s)(?:"'+$taskEscaped+'"|'+$taskEscaped+')(?:\s|$)')
}

function Read-MVState([string]$Name) {
    $taskPath = Join-Path $taskRoot ('.runtime/'+$Name)
    if (-not (Test-Path -LiteralPath $taskPath)) { return $null }
    $taskValue = Get-Content -LiteralPath $taskPath -Raw | ConvertFrom-Json
    if ($taskValue.project -ne $taskRoot) { throw 'Recorded project identity mismatch. No processes stopped.' }
    return $taskValue
}

$taskMVState = Read-MVState 'mv-processes.json'
if ($taskMVState) {
    Add-MVTrackedLauncher 'app' $taskMVState.app_launcher 'start-mv.ps1' $taskMVState.app_launcher_created $true
    Add-MVTrackedLauncher 'proxy' $taskMVState.proxy_launcher 'start-mv-proxy.ps1' $taskMVState.proxy_launcher_created $true
}
$taskOldState = Read-MVState 'processes.json'
if ($taskOldState) {
    # Never read or stop processes.json.app_launcher: that is the basic :8793 app.
    Add-MVTrackedLauncher 'comfy' $taskOldState.comfy_launcher 'start-comfy.ps1' $taskOldState.comfy_launcher_created ([bool]$IncludeComfy)
}
$taskEngineStatePath = Join-Path $taskRoot '.runtime/mv-started-engines.json'
if (Test-Path -LiteralPath $taskEngineStatePath) {
    $taskEngines = Get-Content -LiteralPath $taskEngineStatePath -Raw | ConvertFrom-Json
    foreach ($taskService in @('proxy','comfy')) {
        $taskEntry = $taskEngines.$taskService
        if (-not $taskEntry) { continue }
        $taskScriptName = if ($taskService -eq 'proxy') { 'start-mv-proxy.ps1' } else { 'start-comfy.ps1' }
        if ($taskEntry.script -ne (Join-Path $taskRoot $taskScriptName)) {
            throw 'Recorded engine script identity mismatch. No processes stopped.'
        }
        Add-MVTrackedLauncher $taskService $taskEntry.pid $taskScriptName $taskEntry.created ($taskService -eq 'proxy' -or [bool]$IncludeComfy)
    }
}
if (-not $taskCandidates.Count) {
    Write-Output 'No tracked MV launchers. Stop manually started MV terminals with Ctrl+C.'
    return
}

$taskSnapshot = @(Get-CimInstance Win32_Process)
$taskVerified = @{}
foreach ($taskCandidate in $taskCandidates) {
    $taskProcess = $taskSnapshot | Where-Object ProcessId -eq $taskCandidate.Identifier
    if (-not $taskProcess) { continue }
    $taskFilePattern = '(?i)(?:^|\s)-File\s+(?:"'+[regex]::Escape($taskCandidate.Script)+'"|'+[regex]::Escape($taskCandidate.Script)+')(?:\s|$)'
    if ($taskProcess.Name -notin @('pwsh.exe','powershell.exe') -or $taskProcess.CommandLine -notmatch $taskFilePattern) {
        throw 'Recorded launcher PID now has a different command. No processes stopped.'
    }
    if ($taskCandidate.Created) {
        $taskExpected = Convert-MVCreationUtc $taskCandidate.Created
        if ([Math]::Abs(($taskProcess.CreationDate.ToUniversalTime()-$taskExpected).TotalSeconds) -gt 1) {
            throw 'Recorded launcher creation time changed. No processes stopped.'
        }
    }
    $taskIdentifier = [int]$taskProcess.ProcessId
    if ($taskVerified.ContainsKey($taskIdentifier)) {
        if ($taskVerified[$taskIdentifier].Service -ne $taskCandidate.Service) {
            throw 'One PID is assigned to multiple services. No processes stopped.'
        }
        $taskVerified[$taskIdentifier].ShouldStop = $taskVerified[$taskIdentifier].ShouldStop -or $taskCandidate.ShouldStop
    } else {
        $taskVerified[$taskIdentifier] = [pscustomobject]@{Service=$taskCandidate.Service;
            ShouldStop=$taskCandidate.ShouldStop; Process=$taskProcess; Depth=0}
    }
}
if (-not @($taskVerified.Values | Where-Object ShouldStop).Count) {
    Write-Output 'Tracked MV launchers already exited. No process was stopped.'
    return
}
$taskUntrackedMVApps = @($taskSnapshot | Where-Object {
    $_.Name -eq 'python.exe' -and
    (Test-MVArgument ([string]$_.CommandLine) (Join-Path $taskRoot 'mv_server.py')) -and
    $_.CommandLine -match '(?:^|\s)--port\s+8797(?:\s|$)'
})
if ($taskUntrackedMVApps.Count -and -not @($taskVerified.Values | Where-Object Service -eq 'app').Count) {
    throw 'The MV app was started in a foreground terminal without a tracked launcher. Finish its job and press Ctrl+C there, then run stop-mv.ps1 again. No processes stopped.'
}

# Build only descendants of verified project launchers; never find processes by port.
do {
    $taskAdded = $false
    foreach ($taskProcess in $taskSnapshot) {
        $taskIdentifier = [int]$taskProcess.ProcessId
        $taskParentIdentifier = [int]$taskProcess.ParentProcessId
        if (-not $taskVerified.ContainsKey($taskIdentifier) -and $taskVerified.ContainsKey($taskParentIdentifier)) {
            $taskParent = $taskVerified[$taskParentIdentifier]
            if ($taskProcess.CreationDate -lt $taskParent.Process.CreationDate) {
                throw 'Process ancestry has a reused PID. No processes stopped.'
            }
            $taskVerified[$taskIdentifier] = [pscustomobject]@{Service=$taskParent.Service;
                ShouldStop=$taskParent.ShouldStop; Process=$taskProcess; Depth=($taskParent.Depth+1)}
            $taskAdded = $true
        }
    }
} while ($taskAdded)

# Do not stop an ASR worker, even if its HTTP status has not been written yet.
foreach ($taskEntry in $taskVerified.Values) {
    if ($taskEntry.Service -eq 'app' -and (Test-MVArgument $taskEntry.Process.CommandLine (Join-Path $taskRoot 'src/voice_motion/mv_lyrics.py'))) {
        throw 'This project is transcribing audio. Finish the current MV first. No processes stopped.'
    }
    if ($taskEntry.Depth -eq 0) { continue }
    # Hidden PowerShell launchers can own a Windows console host as well as
    # Python. Accept only the OS binary created with that exact launcher.
    if ($taskEntry.Process.Name -eq 'conhost.exe' -and $taskEntry.Depth -eq 1 -and
        $taskEntry.Process.ExecutablePath -eq ([IO.Path]::GetFullPath((Join-Path ([Environment]::GetFolderPath('Windows')) 'System32/conhost.exe')))) {
        continue
    }
    $taskCommand = [string]$taskEntry.Process.CommandLine
    $taskKnown = $taskEntry.Process.Name -eq 'python.exe'
    if ($taskEntry.Service -eq 'app') {
        $taskKnown = $taskKnown -and (Test-MVArgument $taskCommand (Join-Path $taskRoot 'mv_server.py')) -and $taskCommand -match '(?:^|\s)--port\s+8797(?:\s|$)'
    } elseif ($taskEntry.Service -eq 'proxy') {
        $taskKnown = $taskKnown -and $taskCommand -match '(?:^|\s)-m\s+comfy_api_proxy\.cli(?:\s|$)' -and $taskCommand -match '(?:^|\s)--port\s+8795(?:\s|$)' -and (Test-MVArgument $taskCommand 'http://127.0.0.1:8794')
    } else {
        $taskKnown = $taskKnown -and (Test-MVArgument $taskCommand (Join-Path $taskRoot '.runtime/ComfyUI/main.py')) -and $taskCommand -match '(?:^|\s)--port\s+8794(?:\s|$)' -and (Test-MVArgument $taskCommand (Join-Path $taskRoot 'output'))
    }
    if (-not $taskKnown) { throw ('Unexpected '+$taskEntry.Service+' process PID '+$taskEntry.Process.ProcessId+' under a tracked MV launcher. No processes stopped.') }
}

if (@($taskVerified.Values | Where-Object Service -eq 'proxy').Count -and -not @($taskVerified.Values | Where-Object Service -eq 'comfy').Count) {
    throw 'The proxy is tracked but its isolated Comfy launcher cannot be verified. No processes stopped.'
}
if (@($taskVerified.Values | Where-Object Service -eq 'comfy').Count) {
    $taskQueue = Invoke-RestMethod -Uri 'http://127.0.0.1:8794/queue' -NoProxy -TimeoutSec 5
    if (-not $taskQueue.PSObject.Properties['queue_running'] -or -not $taskQueue.PSObject.Properties['queue_pending']) {
        throw 'Cannot verify the isolated Comfy queue. No processes stopped.'
    }
    if ($taskQueue.queue_running.Count -or $taskQueue.queue_pending.Count) {
        throw 'This project Comfy queue is busy. Finish its current task first. No processes stopped.'
    }
}

if (@($taskVerified.Values | Where-Object Service -eq 'app').Count) {
    $taskHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:8797/api/mv/health' -NoProxy -TimeoutSec 5
    if ($taskHealth.status -ne 'ok' -or $taskHealth.version -ne '0.3.0') {
        throw 'Cannot verify the MV app status. No processes stopped.'
    }
    if ($taskHealth.busy -eq $true) { throw 'The MV app is busy. No processes stopped.' }
    # Health in v0.3 predates a busy flag. Inspect nonterminal project job records
    # against their live API; a 404 is a stale job from a previous app session.
    $taskJobsPath = Join-Path $taskRoot 'output/mv'
    if (Test-Path -LiteralPath $taskJobsPath) {
        foreach ($taskJobDirectory in (Get-ChildItem -LiteralPath $taskJobsPath -Directory)) {
            if ($taskJobDirectory.Name -notmatch '^[A-Za-z0-9][A-Za-z0-9_-]{7,63}$') { continue }
            $taskJobPath = Join-Path $taskJobDirectory.FullName 'job.json'
            if (-not (Test-Path -LiteralPath $taskJobPath)) { continue }
            $taskDiskJob = Get-Content -LiteralPath $taskJobPath -Raw | ConvertFrom-Json
            if ($taskDiskJob.status -in @('succeeded','failed')) { continue }
            try {
                $taskLiveJob = Invoke-RestMethod -Uri ('http://127.0.0.1:8797/api/mv/jobs/'+$taskJobDirectory.Name) -NoProxy -TimeoutSec 5
            } catch {
                if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 404) { continue }
                throw 'Cannot verify a nonterminal MV job. No processes stopped.'
            }
            if ($taskLiveJob.status -notin @('succeeded','failed')) {
                throw 'The MV app has an unfinished job. No processes stopped.'
            }
        }
    }
}

# A final identity check precedes any mutation; children are stopped before parents.
$taskStopEntries = @($taskVerified.Values | Where-Object ShouldStop | Sort-Object Depth -Descending)
foreach ($taskEntry in $taskStopEntries) {
    $taskCurrent = Get-CimInstance Win32_Process -Filter ('ProcessId='+$taskEntry.Process.ProcessId)
    if ($taskCurrent -and ($taskCurrent.CreationDate -ne $taskEntry.Process.CreationDate -or $taskCurrent.CommandLine -ne $taskEntry.Process.CommandLine)) {
        throw 'A tracked process changed during verification. No processes stopped.'
    }
}
foreach ($taskEntry in $taskStopEntries) {
    $taskCurrent = Get-CimInstance Win32_Process -Filter ('ProcessId='+$taskEntry.Process.ProcessId)
    if (-not $taskCurrent) { continue }
    if ($taskCurrent.CreationDate -ne $taskEntry.Process.CreationDate -or $taskCurrent.CommandLine -ne $taskEntry.Process.CommandLine) {
        throw 'Process identity changed. Refusing to stop that PID.'
    }
    if ($PSCmdlet.ShouldProcess(('Voice Motion MV '+$taskEntry.Service+' PID '+$taskCurrent.ProcessId), 'Stop verified project process')) {
        Stop-Process -Id $taskCurrent.ProcessId -Force
    }
}
