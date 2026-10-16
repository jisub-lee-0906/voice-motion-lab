# Pure snapshot regression tests: no process, network or server operations.
$ErrorActionPreference='Stop'
$testProjectRoot=Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $testProjectRoot 'tools/mv_listener_guard.ps1')
$testCases=0

function New-GuardFixture([string]$Service) {
    $testTime=[datetime]'2026-10-17T00:00:00Z'
    if ($Service -eq 'comfy') {
        $testScript=Join-Path $testProjectRoot 'start-comfy.ps1'
        $testCommand='python.exe "'+(Join-Path $testProjectRoot '.runtime/ComfyUI/main.py')+'" --port 8794 --input-directory "'+(Join-Path $testProjectRoot 'input')+'" --output-directory "'+(Join-Path $testProjectRoot 'output')+'"'
        $testPort=8794
    } else {
        $testScript=Join-Path $testProjectRoot 'start-mv-proxy.ps1'
        $testCommand='python.exe -m comfy_api_proxy.cli run --port 8795 --comfyui http://127.0.0.1:8794 --state-dir "'+(Join-Path $testProjectRoot '.runtime/mv-proxy-state')+'"'
        $testPort=8795
    }
    return @{Processes=@(
        [pscustomobject]@{ProcessId=401;ParentProcessId=1;Name='pwsh.exe';CommandLine=('pwsh.exe -File "'+$testScript+'"');CreationDate=$testTime},
        [pscustomobject]@{ProcessId=402;ParentProcessId=401;Name='python.exe';CommandLine=$testCommand;CreationDate=$testTime.AddSeconds(1)},
        [pscustomobject]@{ProcessId=403;ParentProcessId=402;Name='python.exe';CommandLine=$testCommand;CreationDate=$testTime.AddSeconds(2)}
    );Listeners=@([pscustomobject]@{LocalPort=$testPort;LocalAddress='127.0.0.1';OwningProcess=403})}
}

function Assert-GuardRejects([scriptblock]$Operation) {
    $testRejected=$false
    try { & $Operation | Out-Null } catch { $testRejected=$true }
    if (-not $testRejected) { throw 'Unsafe listener fixture was accepted.' }
    $script:testCases++
}

foreach ($testService in @('comfy','proxy')) {
    $testFixture=New-GuardFixture $testService
    $testOwned=Assert-MVListenerOwnership -Root $testProjectRoot -Service $testService @testFixture -ExpectedLauncherId 401 -ExpectedCreated '2026-10-17T00:00:00Z'
    if ($testOwned.launcher_id -ne 401 -or $testOwned.listener_ids[0] -ne 403) { throw 'Valid project ancestry was rejected.' }
    $testCases++
}
$testFixture=New-GuardFixture 'comfy'
$testFixture.Listeners[0].OwningProcess=999
Assert-GuardRejects { Assert-MVListenerOwnership -Root $testProjectRoot -Service 'comfy' @testFixture }
$testFixture=New-GuardFixture 'comfy'
$testFixture.Processes[2].CommandLine='python.exe C:\unrelated\ComfyUI\main.py --port 8794'
Assert-GuardRejects { Assert-MVListenerOwnership -Root $testProjectRoot -Service 'comfy' @testFixture }
$testFixture=New-GuardFixture 'proxy'
$testFixture.Listeners[0].LocalAddress='0.0.0.0'
Assert-GuardRejects { Assert-MVListenerOwnership -Root $testProjectRoot -Service 'proxy' @testFixture }
$testFixture=New-GuardFixture 'comfy'
$testFixture.Processes[1].CreationDate=[datetime]'2026-10-18T00:00:00Z'
Assert-GuardRejects { Assert-MVListenerOwnership -Root $testProjectRoot -Service 'comfy' @testFixture }
$testFixture=New-GuardFixture 'proxy'
Assert-GuardRejects { Assert-MVListenerOwnership -Root $testProjectRoot -Service 'proxy' @testFixture -ExpectedLauncherId 700 }
Assert-GuardRejects { Assert-MVListenerOwnership -Root $testProjectRoot -Service 'proxy' @testFixture -ExpectedCreated '2026-10-18T00:00:00Z' }
$testFixture=New-GuardFixture 'comfy'
$testFixture.Processes[1].ParentProcessId=999
Assert-GuardRejects { Assert-MVListenerOwnership -Root $testProjectRoot -Service 'comfy' @testFixture }
$testFixture=New-GuardFixture 'comfy'
$testFixture.Listeners[0].OwningProcess=401
Assert-GuardRejects { Assert-MVListenerOwnership -Root $testProjectRoot -Service 'comfy' @testFixture }
if ($null -ne (Assert-MVListenerOwnership -Root $testProjectRoot -Service 'comfy' -Processes @() -Listeners @())) { throw 'Missing listener must wait without probing.' }
$testCases++
# Exercise both PowerShell date representations without changing the time zone.
$testExpected=[DateTimeOffset]::Parse('2026-10-16T15:25:12.1704848Z').UtcDateTime
$testJSONDate=('{"created":"2026-10-16T15:25:12.1704848Z"}' | ConvertFrom-Json).created
foreach ($testValue in @($testJSONDate, '2026-10-16T15:25:12.1704848Z',
        [DateTimeOffset]::Parse('2026-10-17T00:25:12.1704848+09:00'),
        '2026-10-17T00:25:12.1704848+09:00')) {
    $testActual=Convert-MVCreationUtc $testValue
    if ($testActual.Kind -ne [DateTimeKind]::Utc -or $testActual.Ticks -ne $testExpected.Ticks) {
        throw 'A JSON/object/string creation timestamp lost its UTC identity.'
    }
    $testCases++
}
$testFixture=New-GuardFixture 'comfy'
$testTypedExpected=('{"created":"2026-10-17T00:00:00Z"}' | ConvertFrom-Json).created
$testOwned=Assert-MVListenerOwnership -Root $testProjectRoot -Service 'comfy' @testFixture -ExpectedCreated $testTypedExpected
if ($testOwned.launcher_id -ne 401) { throw 'Typed JSON creation date failed the ownership check.' }
$testCases++
Assert-GuardRejects { Convert-MVCreationUtc ([DateTime]::SpecifyKind($testExpected, [DateTimeKind]::Unspecified)) }
Assert-GuardRejects { Convert-MVCreationUtc '2026-10-16T15:25:12' }
Write-Output ('MV listener pure checks passed: '+$testCases)
