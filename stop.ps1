[CmdletBinding(SupportsShouldProcess)]
param()
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\','/')
$taskStatePath = Join-Path $taskRoot '.runtime/processes.json'
if (-not (Test-Path -LiteralPath $taskStatePath)) {
    Write-Output 'No recorded background launchers. Stop manually started terminals with Ctrl+C.'
    return
}
$taskState = Get-Content -LiteralPath $taskStatePath -Raw | ConvertFrom-Json
if ($taskState.project -ne $taskRoot) { throw 'Project identity mismatch. No processes stopped.' }
$taskProcesses = @(Get-CimInstance Win32_Process)
$taskIds = [System.Collections.Generic.HashSet[int]]::new()
foreach ($taskPair in @(@($taskState.app_launcher,'start.ps1'),@($taskState.comfy_launcher,'start-comfy.ps1'))) {
    $taskProcess = $taskProcesses | Where-Object ProcessId -eq $taskPair[0]
    if (-not $taskProcess) { continue }
    if ($taskProcess.Name -ne 'pwsh.exe' -or -not $taskProcess.CommandLine.Contains((Join-Path $taskRoot $taskPair[1]))) {
        throw 'Recorded launcher identity changed. No processes stopped.'
    }
    [void]$taskIds.Add([int]$taskProcess.ProcessId)
}
if (-not $taskIds.Count) { Write-Output 'Recorded project launchers already exited.'; return }
if ($taskIds.Contains([int]$taskState.comfy_launcher)) {
    $taskQueue = Invoke-RestMethod -Uri 'http://127.0.0.1:8794/queue' -NoProxy -TimeoutSec 5
    if ($taskQueue.queue_running.Count -or $taskQueue.queue_pending.Count) {
        throw 'This project queue is busy. Finish its current task first. No processes stopped.'
    }
}
do {
    $taskAdded = $false
    foreach ($taskProcess in $taskProcesses) {
        if ($taskIds.Contains([int]$taskProcess.ParentProcessId) -and $taskIds.Add([int]$taskProcess.ProcessId)) { $taskAdded = $true }
    }
} while ($taskAdded)
foreach ($taskProcess in ($taskProcesses | Where-Object { $taskIds.Contains([int]$_.ProcessId) })) {
    $taskCurrent = Get-CimInstance Win32_Process -Filter "ProcessId=$($taskProcess.ProcessId)"
    if ($taskCurrent -and $taskCurrent.CreationDate -eq $taskProcess.CreationDate -and $taskCurrent.CommandLine -eq $taskProcess.CommandLine) {
        if ($PSCmdlet.ShouldProcess("Voice Motion Lab PID $($taskCurrent.ProcessId)", 'Stop verified project process')) {
            Stop-Process -Id $taskCurrent.ProcessId -Force
        }
    }
}
