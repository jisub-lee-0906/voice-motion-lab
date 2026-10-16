$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskTemp = Join-Path $taskRoot 'temp/mv-demo-voice'
New-Item -ItemType Directory -Force -Path $taskTemp | Out-Null
Add-Type -AssemblyName System.Speech
$taskVoice = [System.Speech.Synthesis.SpeechSynthesizer]::new()
$taskLines = @(
    'In the quiet city, I carry a little light.',
    'Through the broken windows, flowers learn to fly.',
    'I keep the pieces of a dream inside my hands.',
    'Let the night fall. We will bloom again.',
    'Round and round, the stars remember every name.',
    'From the cracks we rise, and find our way back home.'
)
try {
    $taskVoice.Rate = -1
    foreach ($taskIndex in 0..($taskLines.Count - 1)) {
        $taskVoice.SetOutputToWaveFile((Join-Path $taskTemp ("line-{0}.wav" -f $taskIndex)))
        $taskVoice.Speak($taskLines[$taskIndex])
        $taskVoice.SetOutputToNull()
    }
} finally { $taskVoice.Dispose() }
& (Join-Path $taskRoot '.mv-venv/Scripts/python.exe') (Join-Path $PSScriptRoot 'create_mv_demo.py')
if ($LASTEXITCODE -ne 0) { throw 'Original synthetic music demo creation failed.' }
