$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    $Python = "python"
}

$RunId = [Guid]::NewGuid().ToString("N")
$BaseTemp = Join-Path $Root ".test-runs\$RunId"
New-Item -ItemType Directory -Force -Path $BaseTemp | Out-Null

& $Python -m pytest -p no:cacheprovider --basetemp $BaseTemp
exit $LASTEXITCODE
