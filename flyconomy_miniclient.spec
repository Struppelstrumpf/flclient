# -*- mode: python ; coding: utf-8 -*-
import os

block_cipher = None
binaries = []

try:
    import SimConnect

    simconnect_path = os.path.dirname(SimConnect.__file__)
    for name in ("SimConnect.dll", "SimConnect64.dll"):
        dll = os.path.join(simconnect_path, name)
        if os.path.exists(dll):
            binaries.append((dll, "."))
except Exception:
    pass

a = Analysis(
    ["flyconomy_miniclient.py"],
    pathex=[],
    binaries=binaries,
    datas=[
        ("ui", "ui"),
        ("tray_icon.png", "."),
        ("logo.png", "."),
        ("logo.ico", "."),
    ],
    hiddenimports=[
        "SimConnect",
        "requests",
        "certifi",
        "pystray",
        "pystray._base",
        "pystray._win32",
        "PIL",
        "PIL.Image",
        "tkinter",
        "_tkinter",
        "flyconomy_client",
        "client_notice",
        "flight_log_ui",
        "sim_ground",
        "cockpit_lamp_test",
        "payware_payload",
        "client_gateway",
        "pypresence",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["webview"],
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
    name="FlyConomyMiniClient",
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
    version="file_version_info.txt",
)
