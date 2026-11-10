# FeatureRail

FeatureRail is a Windows desktop tool for keeping a selected visual feature centered while smoothing camera movement. Select a textured region, track it across a clip, preview the stabilized framing, and export the result as an MP4.

Tracking runs locally with the CPU-based OpenCV KLT tracker. No account, cloud upload, GPU, or model download is required.

## Features

- Select a feature region by dragging in the preview; zoom and pan to make a precise selection.
- Track multiple points in the selected region and follow their median motion.
- Smooth handheld and walking camera movement with adjustable rail stabilization.
- Compare the raw and stabilized views at the current frame.
- Automatically reframe the clip to avoid black borders.
- Export H.264 or H.265 MP4 at the source size or a selected output resolution.
- Preserve source audio when available.
- Cancel tracking and export jobs from the interface.
- Use the interface in 20 languages.

## Download

Download the latest `FeatureRail.exe` from the project's GitHub Releases page. It is a portable, 64-bit Windows application and does not require a separate Python, Qt, OpenCV, or FFmpeg installation. The first launch may take longer while bundled files are extracted.

FeatureRail currently supports Windows 10 and Windows 11 (64-bit).

## Run from source

Requirements: 64-bit Python 3.11 or newer and FFmpeg available on `PATH` for video export.

In PowerShell, from the project directory:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup.ps1
.\run.ps1
```

You can also run `Start FeatureRail.bat` after setup. If FFmpeg is unavailable, video preview and tracking remain usable, while export is disabled until FFmpeg is installed and added to `PATH`.

## Build the portable executable

On 64-bit Windows, run `setup.ps1`, then provide a 64-bit FFmpeg executable and the license file for that exact FFmpeg build:

```powershell
.\build.ps1 -FfmpegPath "C:\path\to\ffmpeg.exe" -FfmpegLicensePath "C:\path\to\FFmpeg-LICENSE.txt"
```

The output is `dist\FeatureRail.exe`. The executable bundles FFmpeg as a separate command-line process, including its license, build configuration, and vendor dependency-version manifest. For a public binary release, use `tools/package_ffmpeg_sources.ps1` to prepare the matching FFmpeg source snapshot and third-party source index for the release assets. Third-party components retain their own licenses; see the [FFmpeg legal page](https://ffmpeg.org/legal.html).

## Interface languages

English, Traditional Chinese, Simplified Chinese, Japanese, Korean, French, German, Spanish, Brazilian Portuguese, Arabic, Russian, Italian, Dutch, Polish, Turkish, Thai, Vietnamese, Indonesian, Malay, and Hindi.

## Use

1. Open a video file.
2. Drag a rectangle around a clear feature with visible texture or edges. Use the mouse wheel to zoom and right-drag to pan.
3. Select **Start Tracking** and wait for tracking to finish.
4. Review the stabilized preview and compare it with the raw view at the same frame.
5. Adjust stabilization or output framing, then select **Export MP4**.

Input format and codec support depends on the available FFmpeg and OpenCV builds. Common formats include MP4, MOV, MKV, and AVI. Export supports H.264 and H.265 with CPU encoding; optional hardware encoders can be used when available in FFmpeg.

## Development

Install the application dependencies and ensure FFmpeg is on `PATH`, then run:

```powershell
python tools/smoke_test.py
```

The smoke test checks tracking, camera-path stabilization, border-safe framing, preview selection and zoom, raw/stabilized comparison, and MP4 export.

## License

FeatureRail is released under the MIT License. See [LICENSE](LICENSE). Third-party components retain their respective licenses; see the notices bundled with the executable and the applicable upstream license terms.
