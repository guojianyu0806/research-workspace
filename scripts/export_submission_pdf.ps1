param([Parameter(Mandatory=$true)][string]$Draft)
$ErrorActionPreference = 'Stop'
$resolvedDraft = (Resolve-Path -LiteralPath $Draft).Path
$word = $null
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $word.AutomationSecurity = 3
    foreach ($file in Get-ChildItem -LiteralPath $resolvedDraft -Filter '*.docx') {
        $pdf = [System.IO.Path]::ChangeExtension($file.FullName, '.pdf')
        if (Test-Path -LiteralPath $pdf) { throw "Refuse to overwrite existing PDF: $pdf" }
        $document = $null
        try {
            $document = $word.Documents.Open($file.FullName, $false, $true, $false)
            $document.Repaginate()
            $document.ExportAsFixedFormat($pdf, 17)
            Write-Output ([System.IO.Path]::GetFileName($pdf))
        } finally {
            if ($null -ne $document) {
                $document.Close(0)
                [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($document)
            }
        }
    }
} finally {
    if ($null -ne $word) {
        $word.Quit()
        [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($word)
    }
}
