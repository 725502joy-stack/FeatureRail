param(
    [Parameter(Mandatory = $true)]
    [string]$BuildInfoPath,
    [Parameter(Mandatory = $true)]
    [string]$LicensePath,
    [Parameter(Mandatory = $true)]
    [string]$OutputPath
)

$ErrorActionPreference = "Stop"
$buildInfo = Get-Content -LiteralPath $BuildInfoPath -Raw
$license = (Resolve-Path -LiteralPath $LicensePath).Path
$sourceMatch = [regex]::Match($buildInfo, 'https://github\.com/FFmpeg/FFmpeg/commit/([0-9a-f]{7,40})')
if (-not $sourceMatch.Success) {
    throw "The FFmpeg build information does not include an exact upstream source commit."
}
$commit = $sourceMatch.Groups[1].Value
$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$workRoot = Join-Path $projectRoot ".build\ffmpeg-source-package"
$stagingRoot = Join-Path $workRoot "staging"
$archivePath = Join-Path $workRoot "ffmpeg-source.zip"
if (Test-Path -LiteralPath $workRoot) {
    $resolvedRoot = [System.IO.Path]::GetFullPath($projectRoot).TrimEnd('\') + '\'
    $resolvedWork = [System.IO.Path]::GetFullPath($workRoot)
    if (-not $resolvedWork.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean a source-package path outside the project."
    }
    Remove-Item -LiteralPath $resolvedWork -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $stagingRoot | Out-Null
$downloadUrl = "https://github.com/FFmpeg/FFmpeg/archive/$commit.zip"
Invoke-WebRequest -Uri $downloadUrl -OutFile $archivePath -TimeoutSec 300
Expand-Archive -LiteralPath $archivePath -DestinationPath $stagingRoot
$sourceDirectory = Get-ChildItem -LiteralPath $stagingRoot -Directory | Select-Object -First 1
if (-not $sourceDirectory) { throw "The FFmpeg source archive is empty." }
$bundleRoot = Join-Path $stagingRoot "FeatureRail-FFmpeg-source-$commit"
Move-Item -LiteralPath $sourceDirectory.FullName -Destination $bundleRoot
Copy-Item -LiteralPath $BuildInfoPath -Destination (Join-Path $bundleRoot "FFmpeg-BUILD-INFO.txt")
Copy-Item -LiteralPath $license -Destination (Join-Path $bundleRoot "FFmpeg-LICENSE.txt")

$externalSources = @'
# FFmpeg external source index

The build manifest in `FFmpeg-BUILD-INFO.txt` records the exact FFmpeg build configuration and the enabled external components. The Gyan build README embedded in that file records the build's external-library versions. Each external project retains its own license and source.

| Component | Upstream source |
| --- | --- |
| Cairo | https://gitlab.freedesktop.org/cairo/cairo |
| Fontconfig | https://gitlab.freedesktop.org/fontconfig/fontconfig |
| FreeType | https://gitlab.freedesktop.org/freetype/freetype |
| FriBidi | https://github.com/fribidi/fribidi |
| AviSynth+ | https://github.com/AviSynth/AviSynthPlus |
| Chromaprint | https://github.com/acoustid/chromaprint |
| GSM | https://github.com/timothytylee/libgsm |
| iLBC | https://github.com/TimothyGu/libilbc |
| LC3 | https://github.com/google/liblc3 |
| SVT-AV1 | https://gitlab.com/AOMediaCodec/SVT-AV1 |
| SVT-JPEG-XS | https://github.com/OpenVisualCloud/SVT-JPEG-XS |
| Lensfun | https://github.com/lensfun/lensfun |
| ModPlug | https://github.com/Konstanty/libmodplug |
| libmysofa | https://github.com/hoene/libmysofa |
| OpenAPV | https://github.com/OpenAPV/OpenAPV-codec |
| ARIB B24 | https://github.com/nkoriyama/aribb24 |
| ARIB caption | https://github.com/xqq/libaribcaption |
| libbs2b | https://github.com/alexmarsev/libbs2b |
| libcaca | https://github.com/cacalabs/libcaca |
| libcdio | https://github.com/libcdio/libcdio |
| Codec2 | https://github.com/drowe67/codec2 |
| Flite | https://github.com/festvox/flite |
| QRencode | https://github.com/fukuchi/libqrencode |
| quirc | https://github.com/dlbeer/quirc |
| Shine | https://github.com/toots/shine |
| Speex | https://github.com/xiph/speex |
| AMR-WB encoder | https://github.com/mstorsjo/vo-amrwbenc |
| zvbi | https://github.com/zapping-vbi/zvbi |
| HarfBuzz | https://github.com/harfbuzz/harfbuzz |
| libxml2 | https://gitlab.gnome.org/GNOME/libxml2 |
| VMAF | https://github.com/Netflix/vmaf |
| shaderc | https://github.com/google/shaderc |
| Vorbis | https://gitlab.xiph.org/xiph/vorbis |
| GMP | https://gmplib.org/ |
| xz / liblzma | https://github.com/tukaani-project/xz |
| libaom | https://aomedia.googlesource.com/aom |
| dav1d | https://code.videolan.org/videolan/dav1d |
| davs2 | https://github.com/pkuvcl/davs2 |
| libdvdread | https://code.videolan.org/videolan/libdvdread |
| libdvdnav | https://code.videolan.org/videolan/libdvdnav |
| frei0r | https://github.com/dyne/frei0r |
| Game Music Emu | https://github.com/libgme/game-music-emu |
| Kvazaar | https://github.com/ultravideo/kvazaar |
| libass | https://github.com/libass/libass |
| libbluray | https://code.videolan.org/videolan/libbluray |
| libjxl | https://github.com/libjxl/libjxl |
| LAME | https://lame.sourceforge.io/ |
| Opus | https://github.com/xiph/opus |
| libplacebo | https://github.com/haasn/libplacebo |
| RIST | https://code.videolan.org/rist/rist |
| libssh | https://www.libssh.org/ |
| Theora | https://gitlab.xiph.org/xiph/theora |
| libvpx | https://chromium.googlesource.com/webm/libvpx |
| libwebp | https://chromium.googlesource.com/webm/libwebp |
| ZeroMQ | https://github.com/zeromq/libzmq |
| LV2 | https://github.com/lv2/lv2 |
| Intel VPL | https://github.com/intel/libvpl |
| OpenAL Soft | https://github.com/kcat/openal-soft |
| OpenCORE AMR | https://sourceforge.net/projects/opencore-amr/ |
| OpenH264 | https://github.com/cisco/openh264 |
| OpenJPEG | https://github.com/uclouvain/openjpeg |
| OpenMPT | https://github.com/OpenMPT/openmpt |
| rav1e | https://github.com/xiph/rav1e |
| Rubber Band | https://github.com/breakfastquay/rubberband |
| SDL | https://github.com/libsdl-org/SDL |
| Snappy | https://github.com/google/snappy |
| SoX Resampler | https://sourceforge.net/projects/soxr/ |
| SRT | https://github.com/Haivision/srt |
| SVT-AV1 | https://gitlab.com/AOMediaCodec/SVT-AV1 |
| TwoLAME | https://github.com/njh/twolame |
| uavs3d | https://github.com/uavs3/uavs3d |
| vid.stab | https://github.com/georgmartius/vid.stab |
| vvenc | https://github.com/fraunhoferhhi/vvenc |
| whisper.cpp | https://github.com/ggml-org/whisper.cpp |
| x264 | https://code.videolan.org/videolan/x264 |
| x265 | https://bitbucket.org/multicoreware/x265_git |
| xavs2 | https://github.com/pkuvcl/xavs2 |
| Xvid | https://labs.xvid.com/source/ |
| zimg | https://github.com/sekrit-twc/zimg |
| zvbi | https://github.com/zapping-vbi/zvbi |
| AMF headers | https://github.com/GPUOpen-LibrariesAndSDKs/AMF |
| NVIDIA codec headers | https://github.com/FFmpeg/nv-codec-headers |
| Vulkan headers and loader | https://github.com/KhronosGroup/Vulkan-Headers |
'@
Set-Content -LiteralPath (Join-Path $bundleRoot "THIRD-PARTY-SOURCES.md") -Value $externalSources -Encoding utf8
$output = [System.IO.Path]::GetFullPath($OutputPath)
$outputParent = Split-Path -Parent $output
New-Item -ItemType Directory -Force -Path $outputParent | Out-Null
if (Test-Path -LiteralPath $output) { Remove-Item -LiteralPath $output -Force }
Compress-Archive -Path (Join-Path $bundleRoot '*') -DestinationPath $output -CompressionLevel Optimal
Write-Host "Created $output" -ForegroundColor Green
