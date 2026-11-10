# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path


project_root = Path(SPECPATH)
ffmpeg_path = Path(os.environ["FEATURERAIL_FFMPEG"]).resolve()
notices_path = Path(os.environ["FEATURERAIL_NOTICES"]).resolve()
ffmpeg_license_path = Path(os.environ["FEATURERAIL_FFMPEG_LICENSE"]).resolve()
ffmpeg_build_info_path = Path(os.environ["FEATURERAIL_FFMPEG_BUILD_INFO"]).resolve()

for required_file in (ffmpeg_path, notices_path, ffmpeg_license_path, ffmpeg_build_info_path):
    if not required_file.is_file():
        raise FileNotFoundError(f"Required packaging file not found: {required_file}")

a = Analysis(
    [str(project_root / "main.py")],
    pathex=[str(project_root)],
    binaries=[(str(ffmpeg_path), ".")],
    datas=[
        (str(notices_path), "licenses"),
        (str(ffmpeg_license_path), "licenses"),
        (str(ffmpeg_build_info_path), "licenses"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="FeatureRail",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
