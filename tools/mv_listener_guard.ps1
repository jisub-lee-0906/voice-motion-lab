# This file defines guards only. Dot-sourcing it never starts or probes a server.
function Convert-MVCreationUtc($Value) {
    # Recent PowerShell ConvertFrom-Json materializes ISO dates as DateTime.
    # Converting that object through [string] first drops its UTC/offset marker.
    if ($Value -is [DateTimeOffset]) { return $Value.UtcDateTime }
    if ($Value -is [DateTime]) {
        if ($Value.Kind -eq [DateTimeKind]::Unspecified) {
            throw 'Creation timestamp has no time zone; refusing ambiguous process identity.'
        }
        return $Value.ToUniversalTime()
    }
    if ($Value -isnot [string] -or $Value -notmatch '(?i)(?:Z|[+-]\d{2}:\d{2})$') {
        throw 'Creation timestamp must include an explicit time zone.'
    }
    return [DateTimeOffset]::Parse($Value, [Globalization.CultureInfo]::InvariantCulture).UtcDateTime
}

function Test-MVListenerArgument([string]$Command, [string]$Argument) {
    if ($Argument -match '^[A-Za-z]:[\\/]') {
        $Command=$Command.Replace('/','\'); $Argument=$Argument.Replace('/','\')
    }
    $taskEscaped=[regex]::Escape($Argument)
    return $Command -match ('(?i)(?:^|\s)(?:"'+$taskEscaped+'"|'+$taskEscaped+')(?:\s|$)')
}

function Test-MVListenerPython($Process, [string]$Root, [string]$Service) {
    if ($Process.Name -ne 'python.exe') { return $false }
    $taskCommand=[string]$Process.CommandLine
    if ($Service -eq 'comfy') {
        return (Test-MVListenerArgument $taskCommand (Join-Path $Root '.runtime/ComfyUI/main.py')) -and
            $taskCommand -match '(?:^|\s)--port\s+8794(?:\s|$)' -and
            (Test-MVListenerArgument $taskCommand (Join-Path $Root 'output')) -and
            (Test-MVListenerArgument $taskCommand (Join-Path $Root 'input'))
    }
    return $taskCommand -match '(?:^|\s)-m\s+comfy_api_proxy\.cli(?:\s|$)' -and
        $taskCommand -match '(?:^|\s)--port\s+8795(?:\s|$)' -and
        (Test-MVListenerArgument $taskCommand 'http://127.0.0.1:8794') -and
        (Test-MVListenerArgument $taskCommand (Join-Path $Root '.runtime/mv-proxy-state'))
}

function Assert-MVListenerOwnership {
    # Pure snapshot check: no CIM, network, file writes, launch or stop calls.
    param([string]$Root, [ValidateSet('comfy','proxy')][string]$Service,
          [object[]]$Processes, [object[]]$Listeners,
          [int]$ExpectedLauncherId=0, $ExpectedCreated=$null)
    $taskPort=if ($Service -eq 'comfy') {8794} else {8795}
    $taskScript=if ($Service -eq 'comfy') {'start-comfy.ps1'} else {'start-mv-proxy.ps1'}
    $taskScriptPath=[IO.Path]::GetFullPath((Join-Path $Root $taskScript))
    $taskScriptPattern='(?i)(?:^|\s)-File\s+(?:"'+[regex]::Escape($taskScriptPath)+'"|'+[regex]::Escape($taskScriptPath)+')(?:\s|$)'
    if (-not $Listeners.Count) { return $null }
    $taskProcessesById=@{}
    foreach ($taskProcess in $Processes) {
        $taskIdentifier=[int]$taskProcess.ProcessId
        if ($taskProcessesById.ContainsKey($taskIdentifier)) { throw 'Ambiguous process snapshot; refusing the MV listener.' }
        $taskProcessesById[$taskIdentifier]=$taskProcess
    }
    $taskOwners=[System.Collections.Generic.HashSet[int]]::new()
    $taskLauncherIds=[System.Collections.Generic.HashSet[int]]::new()
    foreach ($taskListener in $Listeners) {
        if ([int]$taskListener.LocalPort -ne $taskPort -or $taskListener.LocalAddress -notin @('127.0.0.1','::1')) {
            throw 'The MV listener is not restricted to its expected loopback port.'
        }
        $taskIdentifier=[int]$taskListener.OwningProcess
        if (-not $taskProcessesById.ContainsKey($taskIdentifier)) { throw 'Cannot identify the actual MV port owner.' }
        [void]$taskOwners.Add($taskIdentifier)
        $taskCurrent=$taskProcessesById[$taskIdentifier]
        if (-not (Test-MVListenerPython $taskCurrent $Root $Service)) {
            throw 'The actual listener must be this project Python service entry point.'
        }
        $taskVisited=[System.Collections.Generic.HashSet[int]]::new()
        $taskFound=$false
        while ($taskCurrent) {
            if (-not $taskVisited.Add([int]$taskCurrent.ProcessId)) { throw 'Cyclic process ancestry; refusing the MV listener.' }
            if (-not $taskCurrent.CreationDate) { throw 'Cannot verify MV process creation time.' }
            if ($taskCurrent.Name -in @('pwsh.exe','powershell.exe') -and $taskCurrent.CommandLine -match $taskScriptPattern) {
                if ($ExpectedLauncherId -and [int]$taskCurrent.ProcessId -ne $ExpectedLauncherId) {
                    throw 'The MV listener belongs to a different launcher.'
                }
                if ($ExpectedCreated -and [Math]::Abs(($taskCurrent.CreationDate.ToUniversalTime()-(Convert-MVCreationUtc $ExpectedCreated)).TotalSeconds) -gt 1) {
                    throw 'The MV launcher PID was reused.'
                }
                [void]$taskLauncherIds.Add([int]$taskCurrent.ProcessId)
                $taskFound=$true
                break
            }
            if (-not (Test-MVListenerPython $taskCurrent $Root $Service)) {
                throw 'The actual port owner is not this project service entry point.'
            }
            $taskParentIdentifier=[int]$taskCurrent.ParentProcessId
            if (-not $taskProcessesById.ContainsKey($taskParentIdentifier)) { break }
            $taskParent=$taskProcessesById[$taskParentIdentifier]
            if (-not $taskParent.CreationDate -or $taskCurrent.CreationDate -lt $taskParent.CreationDate) {
                throw 'The MV ancestry contains a reused parent PID.'
            }
            $taskCurrent=$taskParent
        }
        if (-not $taskFound) { throw 'The actual listener is not a descendant of this project launcher.' }
    }
    if ($taskLauncherIds.Count -ne 1) { throw 'Multiple MV launchers claim the same service port.' }
    if (-not @($Listeners | Where-Object LocalAddress -eq '127.0.0.1').Count) {
        throw 'The expected IPv4 loopback MV listener is missing.'
    }
    $taskLauncherId=@($taskLauncherIds)[0]
    return [pscustomobject]@{port=$taskPort; launcher_id=$taskLauncherId;
        created=$taskProcessesById[$taskLauncherId].CreationDate.ToUniversalTime().ToString('o');
        listener_ids=@($taskOwners)}
}

function Get-MVListenerOwnership {
    param([string]$Root, [ValidateSet('comfy','proxy')][string]$Service,
          [int]$ExpectedLauncherId=0, $ExpectedCreated=$null)
    $taskPort=if ($Service -eq 'comfy') {8794} else {8795}
    # Query actual OwningProcess; finding an unrelated matching script elsewhere
    # in the process table is insufficient. Access failure remains a hard error.
    $taskConnections=@(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object LocalPort -eq $taskPort)
    if (-not $taskConnections.Count) { return $null }
    $taskProcesses=@(Get-CimInstance Win32_Process -ErrorAction Stop)
    return Assert-MVListenerOwnership -Root $Root -Service $Service -Processes $taskProcesses -Listeners $taskConnections -ExpectedLauncherId $ExpectedLauncherId -ExpectedCreated $ExpectedCreated
}
