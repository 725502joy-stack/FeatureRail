$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot
$environment = Join-Path $projectRoot ".venv"

function Test-PythonCandidate([string]$command, [string[]]$versionArgs) {
    $probe = & $command @versionArgs -c "import sys; print(int(sys.version_info >= (3, 11) and sys.maxsize > 2**32))" 2>$null
    return ($LASTEXITCODE -eq 0 -and "$probe".Trim() -eq "1")
}

if (Test-Path (Join-Path $environment "Scripts\python.exe")) {
    $pythonExe = Join-Path $environment "Scripts\python.exe"
    if (-not (Test-PythonCandidate $pythonExe @())) {
        $installedVersion = (& $pythonExe --version).Trim()
        throw ".venv uses $installedVersion. FeatureRail requires 64-bit Python 3.11 or newer."
    }
} else {
    $pythonCommand = $null
    $versionArgs = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        if (Test-PythonCandidate "py" @("-3.12")) {
            $pythonCommand = "py"
            $versionArgs = @("-3.12")
        } elseif (Test-PythonCandidate "py" @()) {
            $pythonCommand = "py"
        }
    }
    if (-not $pythonCommand -and (Get-Command python -ErrorAction SilentlyContinue) -and (Test-PythonCandidate "python" @())) {
        $pythonCommand = "python"
    }
    if (-not $pythonCommand) {
        throw "FeatureRail requires 64-bit Python 3.11 or newer. Install Python and make sure 'py' or 'python' is available, then run this script again."
    }
    $venvArgs = @($versionArgs) + @("-m", "venv", $environment)
    & $pythonCommand @venvArgs
    if ($LASTEXITCODE -ne 0) { throw "Failed to create the Python virtual environment." }
    $pythonExe = Join-Path $environment "Scripts\python.exe"
}

Write-Host "Installing FeatureRail dependencies..." -ForegroundColor Cyan
& $pythonExe -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Failed to update pip." }
& $pythonExe -m pip install -r (Join-Path $projectRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Failed to install application dependencies." }

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Warning "FFmpeg was not found. Preview and tracking remain available; install FFmpeg and add ffmpeg.exe to PATH to export MP4 video."
} else {
    Write-Host "FFmpeg: $(Get-Command ffmpeg | Select-Object -ExpandProperty Source)" -ForegroundColor DarkGray
}

Write-Host "Setup complete. Run .\run.ps1 to open FeatureRail." -ForegroundColor Green
