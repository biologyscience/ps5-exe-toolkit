# -*- mode: python ; coding: utf-8 -*-

import os

datas = [
    (
        os.path.join(SPECPATH, "assets", "fonts", filename),
        os.path.join("assets", "fonts"),
    )
    for filename in (
        "Ubuntu-Regular.ttf",
        "Ubuntu-Medium.ttf",
        "UbuntuMono-Regular.ttf",
        "LICENCE.txt",
    )
] + [
    (os.path.join(SPECPATH, "assets", filename), "assets")
    for filename in ("app-icon.png", "app-icon.svg")
]
binaries = []
hiddenimports = []


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='PS5 EXE Toolkit',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    icon=os.path.join(SPECPATH, "assets", "app-icon.ico"),
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
