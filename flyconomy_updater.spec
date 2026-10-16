# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ["flyconomy_updater.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("ui/updater.html", "ui"),
        ("logo.ico", "."),
        ("tray_icon.png", "."),
    ],
    hiddenimports=[
        "flyconomy_webapp",
        "requests",
        "certifi",
        "webview",
        "bottle",
        "clr_loader",
        "pythonnet",
        "pystray",
        "pystray._base",
        "pystray._win32",
        "PIL",
        "PIL.Image",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["SimConnect"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="FlyConomyUpdater",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="logo.ico",
    version="file_version_info_updater.txt",
)
