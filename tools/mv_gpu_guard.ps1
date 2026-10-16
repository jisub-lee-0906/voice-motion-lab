# Read-only resource checks; importing this file starts no process or inference.
function Get-MVQualityResourceState {
    $qualityRows=@(& nvidia-smi --query-gpu=index,name,memory.free,utilization.gpu,driver_version --format=csv,noheader,nounits)
    if ($LASTEXITCODE -ne 0 -or -not $qualityRows.Count) { throw 'Cannot inspect GPU resources; refusing setup/start.' }
    $qualityFields=([string]$qualityRows[0]).Split(',') | ForEach-Object {$_.Trim()}
    if ($qualityFields.Count -ne 5 -or $qualityFields[0] -ne '0') { throw 'Unexpected GPU inventory; refusing implicit device selection.' }
    $qualityFree=[double]::Parse($qualityFields[2],[Globalization.CultureInfo]::InvariantCulture)
    $qualityUse=[double]::Parse($qualityFields[3],[Globalization.CultureInfo]::InvariantCulture)
    $qualityApps=@(& nvidia-smi --query-compute-apps=pid,process_name --format=csv,noheader,nounits)
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect active GPU compute processes; refusing setup/start.' }
    $qualityApps=@($qualityApps | Where-Object {$_ -match '^\s*\d+\s*,'})
    $qualityOS=Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
    return [pscustomobject]@{gpu=$qualityFields[1];free_vram_gib=($qualityFree/1024);
        utilization=$qualityUse;driver=$qualityFields[4];compute_process_count=$qualityApps.Count;
        free_ram_gib=([double]$qualityOS.FreePhysicalMemory/1024/1024)}
}

function Assert-MVQualityResourceWindow([switch]$ResourceApproved) {
    if (-not $ResourceApproved) { throw 'An explicit resource-window approval is required. Never unload or interrupt existing work.' }
    $qualityState=Get-MVQualityResourceState
    if ($qualityState.free_vram_gib -lt 12 -or $qualityState.free_ram_gib -lt 16 -or
        $qualityState.utilization -gt 5 -or $qualityState.compute_process_count -ne 0) {
        throw ('Shared resources are occupied: '+($qualityState | ConvertTo-Json -Compress)+'. No install/start performed; never stop other work.')
    }
    return $qualityState
}

function Test-MVQualityArgument([string]$Command,[string]$Argument) {
    if ($Argument -match '^[A-Za-z]:[\\/]') {$Command=$Command.Replace('/','\');$Argument=$Argument.Replace('/','\')}
    $qualityEscaped=[regex]::Escape($Argument)
    return $Command -match ('(?i)(?:^|\s)(?:"'+$qualityEscaped+'"|'+$qualityEscaped+')(?:\s|$)')
}

function Assert-MVQualityListener([string]$Root) {
    $qualityConnections=@(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object LocalPort -eq 8796)
    if (-not $qualityConnections.Count) {throw 'The dedicated project GPU listener on 8796 is missing.'}
    $qualityProcesses=@(Get-CimInstance Win32_Process -ErrorAction Stop)
    $qualityIndex=@{}
    foreach ($qualityProcess in $qualityProcesses) {$qualityIndex[[int]$qualityProcess.ProcessId]=$qualityProcess}
    foreach ($qualityConnection in $qualityConnections) {
        if ($qualityConnection.LocalAddress -ne '127.0.0.1') {throw 'GPU benchmark listener is not isolated to IPv4 loopback.'}
        $qualityCurrent=$qualityIndex[[int]$qualityConnection.OwningProcess]
        if (-not $qualityCurrent -or $qualityCurrent.Name -ne 'python.exe') {throw 'The GPU port must be owned by the dedicated Python entry point.'}
        $qualityVisited=[System.Collections.Generic.HashSet[int]]::new()
        $qualityFound=$false
        while ($qualityCurrent) {
            if (-not $qualityVisited.Add([int]$qualityCurrent.ProcessId)) {throw 'Cyclic GPU process ancestry.'}
            if ($qualityCurrent.Name -in @('pwsh.exe','powershell.exe') -and
                $qualityCurrent.CommandLine -match ('(?i)(?:^|\s)-File\s+(?:"'+[regex]::Escape((Join-Path $Root 'start-mv-gpu.ps1'))+'"|'+[regex]::Escape((Join-Path $Root 'start-mv-gpu.ps1'))+')(?:\s|$)')) {
                $qualityFound=$true;break
            }
            if ($qualityCurrent.Name -ne 'python.exe' -or
                -not (Test-MVQualityArgument $qualityCurrent.CommandLine (Join-Path $Root '.runtime/ComfyUI/main.py')) -or
                $qualityCurrent.CommandLine -notmatch '(?:^|\s)--port\s+8796(?:\s|$)' -or
                -not (Test-MVQualityArgument $qualityCurrent.CommandLine (Join-Path $Root '.runtime/mv-quality/models')) -or
                -not (Test-MVQualityArgument $qualityCurrent.CommandLine (Join-Path $Root 'input/mv-quality')) -or
                -not (Test-MVQualityArgument $qualityCurrent.CommandLine (Join-Path $Root 'output/mv-quality'))) {
                throw 'Actual GPU port owner is not this project dedicated Comfy entry point.'
            }
            $qualityParent=$qualityIndex[[int]$qualityCurrent.ParentProcessId]
            if (-not $qualityParent -or -not $qualityCurrent.CreationDate -or -not $qualityParent.CreationDate -or
                $qualityCurrent.CreationDate -lt $qualityParent.CreationDate) {throw 'GPU launcher ancestry cannot be verified.'}
            $qualityCurrent=$qualityParent
        }
        if (-not $qualityFound) {throw 'GPU listener does not descend from a verified project launcher.'}
    }
    return $true
}
