param([switch]$Quick)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$driverPath = Join-Path $PSScriptRoot 'concurrency_acceptance.py'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Project Python is unavailable.' }
$stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffffffZ')
$launchRoot = Join-Path $projectRoot ('artifacts\concurrency-launchers\' + $stamp)
if (Test-Path -LiteralPath $launchRoot) { throw 'Refusing to replace an existing launch.' }
New-Item -ItemType Directory -Path $launchRoot | Out-Null
$stdoutPath = Join-Path $launchRoot 'stdout.log'
$stderrPath = Join-Path $launchRoot 'stderr.log'
$driverArguments = @('-u', ('"{0}"' -f $driverPath))
if ($Quick) { $driverArguments += '--quick' }
$child = Start-Process -FilePath $pythonPath -ArgumentList $driverArguments -WorkingDirectory $projectRoot `
    -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
$metadata = [ordered]@{
    launcher_pid = $child.Id
    started_at = $child.StartTime.ToUniversalTime().ToString('o')
    mode = $(if ($Quick) {'preflight_not_sustained_acceptance'} else {'full_sustained_acceptance'})
    stdout = $stdoutPath
    stderr = $stderrPath
    source_revision = (git -C $projectRoot rev-parse HEAD)
    note = 'Independent hidden process; verify the live PID and final report. A shutdown interrupts acceptance.'
}
$metadata | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $launchRoot 'launch.json') -Encoding utf8
$metadata | ConvertTo-Json
