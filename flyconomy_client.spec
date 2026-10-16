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
    ["flyconomy_client.py"],
    pathex=[],
    binaries=binaries,
    datas=[("ui", "ui")],
    hiddenimports=[
        "SimConnect",
        "requests",
        "certifi",
        "webview",
        "bottle",
        "clr_loader",
        "pythonnet",
        "cockpit_lamp_test",
        "payware_payload",
        "discord_presence",
        "client_gateway",
        "client_notice",
        "flight_log_ui",
        "pypresence",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name="FlyConomyClient",
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
    version="file_version_info_client.txt",
)
