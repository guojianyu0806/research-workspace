param([ValidateRange(1024,65535)][int]$Port = 8765)
$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
$pythonExecutable = Join-Path $projectDirectory '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExecutable)) {
    throw '未找到项目虚拟环境。请先按 README 准备依赖。'
}
Push-Location -LiteralPath $projectDirectory
try {
    $localOcrDirectory = Join-Path $projectDirectory 'data\ocr\tessdata'
    if (-not $env:RESEARCH_OCR_TESSDATA -and (Test-Path -LiteralPath (Join-Path $localOcrDirectory 'eng.traineddata'))) {
        $env:RESEARCH_OCR_TESSDATA = $localOcrDirectory
        if (-not $env:RESEARCH_OCR_LANG) { $env:RESEARCH_OCR_LANG = 'eng+chi_sim' }
    }
    Write-Host "研序本地工作台：http://127.0.0.1:$Port"
    Write-Host '按 Ctrl+C 停止。当前为本地单工作空间，不提供公网服务。'
    & $pythonExecutable -m uvicorn research_workspace.api:app --host 127.0.0.1 --port $Port --workers 1
    if ($LASTEXITCODE -ne 0) { throw "服务退出，代码：$LASTEXITCODE" }
} finally {
    Pop-Location
}
