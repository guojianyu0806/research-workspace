param(
    [Parameter(Mandatory=$true)][string]$ScenesFile,
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [ValidateRange(-3,3)][int]$Rate=0
)
$ErrorActionPreference = 'Stop'
$sourcePath = (Resolve-Path -LiteralPath $ScenesFile).Path
$targetPath = [IO.Path]::GetFullPath($OutputDirectory)
$projectPath = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
if (-not $targetPath.StartsWith($projectPath + '\')) { throw 'Narration output must remain in this project.' }
if (Test-Path -LiteralPath $targetPath) { throw 'Refuse to overwrite an existing narration directory.' }
$data = Get-Content -LiteralPath $sourcePath -Raw -Encoding utf8 | ConvertFrom-Json
$scenes = if ($data.scenes) { @($data.scenes) } else { @($data) }
if ($scenes.Count -lt 2) { throw 'Expected at least two narration scenes.' }
New-Item -ItemType Directory -Path $targetPath | Out-Null
$speaker = $null
try {
    $speaker = New-Object -ComObject SAPI.SpVoice
    $matches = @($speaker.GetVoices() | Where-Object { $_.GetDescription() -like '*Huihui*' })
    if ($matches.Count -ne 1) { throw 'The installed Chinese Huihui voice was not uniquely found.' }
    $speaker.Voice = $matches[0]
    $speaker.Rate = $Rate
    $speaker.Volume = 100
    $records = @()
    for ($index=0; $index -lt $scenes.Count; $index++) {
        $text = [string]$scenes[$index].narration
        if ([string]::IsNullOrWhiteSpace($text)) { throw "Missing narration at scene $index" }
        $filename = '{0:d2}.wav' -f ($index+1)
        $wavePath = Join-Path $targetPath $filename
        $stream = $null
        try {
            $stream = New-Object -ComObject SAPI.SpFileStream
            $stream.Open($wavePath,3,$false)
            $speaker.AudioOutputStream = $stream
            [void]$speaker.Speak($text,0)
            $stream.Close()
        } finally {
            if ($null -ne $stream) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($stream) }
        }
        $records += [ordered]@{scene=$index+1;title=$scenes[$index].title;file=$filename;characters=$text.Length}
    }
    $report = [ordered]@{voice=$speaker.Voice.GetDescription();rate=$Rate;offline=$true;voice_cloning=$false;audio_only_not_video=$true;scenes=$records}
    $report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $targetPath 'speech-report.json') -Encoding utf8
    Write-Output ($report | ConvertTo-Json -Depth 6 -Compress)
} finally {
    if ($null -ne $speaker) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($speaker) }
}
