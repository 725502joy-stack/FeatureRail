param(
    [Parameter(Mandatory = $true)]
    [string]$FfmpegPath,
    [string]$FfmpegLicensePath
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    throw "Project environment not found. Run .\setup.ps1 first."
}

$ffmpegExe = (Resolve-Path -LiteralPath $FfmpegPath).Path
if (-not (Test-Path -LiteralPath $ffmpegExe -PathType Leaf)) {
    throw "FFmpeg executable not found: $FfmpegPath"
}

if (-not $FfmpegLicensePath) {
    $ffmpegBuildRoot = Split-Path -Parent (Split-Path -Parent $ffmpegExe)
    $candidateLicense = Join-Path $ffmpegBuildRoot "LICENSE"
    if (Test-Path -LiteralPath $candidateLicense) {
        $FfmpegLicensePath = $candidateLicense
    }
}
if (-not $FfmpegLicensePath -or -not (Test-Path -LiteralPath $FfmpegLicensePath -PathType Leaf)) {
    throw "Provide the license file for this exact FFmpeg build with -FfmpegLicensePath."
}
$ffmpegLicense = (Resolve-Path -LiteralPath $FfmpegLicensePath).Path

$versionLines = @(& $ffmpegExe -version 2>&1)
if ($LASTEXITCODE -ne 0 -or $versionLines.Count -eq 0) {
    throw "The provided FFmpeg executable could not be started."
}
$ffmpegVersion = [string]$versionLines[0]
$buildRoot = Join-Path $projectRoot ".build"
New-Item -ItemType Directory -Force -Path $buildRoot | Out-Null
$noticesPath = Join-Path $buildRoot "THIRD_PARTY_NOTICES.txt"
$licenseCopyPath = Join-Path $buildRoot "FFmpeg-LICENSE.txt"
$buildInfoPath = Join-Path $buildRoot "FFmpeg-BUILD-INFO.txt"
Copy-Item -LiteralPath $ffmpegLicense -Destination $licenseCopyPath -Force

$readmePath = Join-Path (Split-Path -Parent (Split-Path -Parent $ffmpegExe)) "README.txt"
$sourceLine = "Source code: Check the exact FFmpeg build's accompanying README or vendor release page."
if (Test-Path -LiteralPath $readmePath) {
    $readmeSource = Select-String -Path $readmePath -Pattern '^Source Code:\s*(.+)$' | Select-Object -First 1
    if ($readmeSource) { $sourceLine = "Source code: $($readmeSource.Matches[0].Groups[1].Value.Trim())" }
}
$buildConfOutPath = Join-Path $buildRoot "FFmpeg-buildconf.stdout.txt"
$buildConfErrorPath = Join-Path $buildRoot "FFmpeg-buildconf.stderr.txt"
$buildConfProcess = Start-Process -FilePath $ffmpegExe -ArgumentList "-buildconf" -NoNewWindow -Wait -PassThru -RedirectStandardOutput $buildConfOutPath -RedirectStandardError $buildConfErrorPath
if ($buildConfProcess.ExitCode -ne 0) { throw "Failed to read the FFmpeg build configuration." }
$buildConf = (@(Get-Content -LiteralPath $buildConfOutPath) + @(Get-Content -LiteralPath $buildConfErrorPath)) -join "`n"
$vendorReadme = if (Test-Path -LiteralPath $readmePath) { Get-Content -LiteralPath $readmePath -Raw } else { "Vendor build README was not supplied." }
@"
FeatureRail bundled FFmpeg build information

$ffmpegVersion
$sourceLine

Exact configure/build options reported by the bundled binary:
$buildConf

Vendor build README and external library version list:
$vendorReadme
"@ | Set-Content -LiteralPath $buildInfoPath -Encoding utf8

$noticeText = @"
FeatureRail is licensed under the MIT License. See the project LICENSE.

This executable bundles FFmpeg as a separate command-line program and starts it
as a separate process for video export. The bundled build may have additional
license obligations. Its build identity is:

$ffmpegVersion
$sourceLine

The license text supplied for this exact FFmpeg build is included as
FFmpeg-LICENSE.txt in this bundle. FFmpeg source and build information must be
provided with public releases as required by the FFmpeg build's license and
the licenses of its enabled external libraries. See https://ffmpeg.org/legal.html.
"@
Set-Content -LiteralPath $noticesPath -Value $noticeText -Encoding utf8

$env:FEATURERAIL_FFMPEG = $ffmpegExe
$env:FEATURERAIL_NOTICES = $noticesPath
$env:FEATURERAIL_FFMPEG_LICENSE = $licenseCopyPath
$env:FEATURERAIL_FFMPEG_BUILD_INFO = $buildInfoPath

Write-Host "Installing build tooling..." -ForegroundColor Cyan
& $pythonExe -m pip install -r (Join-Path $projectRoot "requirements-build.txt")
if ($LASTEXITCODE -ne 0) { throw "Installing build tooling failed." }

Write-Host "Building the standalone Windows executable..." -ForegroundColor Cyan
& $pythonExe -m PyInstaller --noconfirm --clean (Join-Path $projectRoot "FeatureRail.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }

$outputExe = Join-Path $projectRoot "dist\FeatureRail.exe"
if (-not (Test-Path -LiteralPath $outputExe -PathType Leaf)) {
    throw "Build completed without producing the expected executable."
}
$hash = (Get-FileHash -LiteralPath $outputExe -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath "$outputExe.sha256" -Value "$hash  FeatureRail.exe" -Encoding ascii
Write-Host "Created $outputExe" -ForegroundColor Green
Write-Host "Created $outputExe.sha256" -ForegroundColor Green
Write-Host "The executable includes a 64-bit FFmpeg build. Review its license and source obligations before publishing." -ForegroundColor Yellow
