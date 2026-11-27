# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for bacpro.

Build:
    pyinstaller bacpro.spec --noconfirm

Produces a single self-contained executable in dist/:
    dist/bacpro          (Linux / macOS)
    dist/bacpro.exe      (Windows)
"""

import os
import sys

block_cipher = None

ROOT = os.path.abspath(os.getcwd())

# The whole web UI is bundled next to the executable and unpacked at runtime
# into sys._MEIPASS/static (see server._static_dir()).
datas = [
    (os.path.join(ROOT, "static"), "static"),
    (os.path.join(ROOT, "assets"), "assets"),
]

hiddenimports = [
    "serial",
    "serial.tools",
    "serial.tools.list_ports",
    "serial.urlhandler",
    "serial.urlhandler.protocol_socket",
    "flask",
    "jinja2",
    "werkzeug",
]

a = Analysis(
    ["run.py"],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "numpy",
        "pandas",
        "scipy",
        "PIL.ImageQt",
        "pytest",
    ],
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
    name="bacpro",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # windowed app: no console window on Windows
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, "assets", "icon.ico"),
)
