$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    & (Join-Path $projectRoot "setup.ps1")
}
if (-not (Test-Path $pythonExe)) { throw "The application environment could not be found. Run setup.ps1 again." }
& $pythonExe (Join-Path $projectRoot "main.py")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
