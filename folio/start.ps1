$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$python = Join-Path $PSScriptRoot "backend\.venv\Scripts\python.exe"
if (!(Test-Path $python)) {
    py -m venv backend\.venv
    if ($LASTEXITCODE -ne 0) { throw "Python 3.11+ is required." }
}
& $python -m pip install -r backend\requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Python dependency installation failed." }
Write-Host "Starting Folio at http://127.0.0.1:8000"
Set-Location backend
& $python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
