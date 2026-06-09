# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata


repo_root = Path(SPECPATH).parents[1]
entry = Path(SPECPATH) / "renderdoc_mcp_entry.py"

hiddenimports = []
datas = []

for package in ("fastmcp", "mcp", "pydantic"):
    try:
        hiddenimports += collect_submodules(package)
    except Exception:
        pass

for package in ("fastmcp", "mcp", "pydantic"):
    try:
        datas += collect_data_files(package)
    except Exception:
        pass
    try:
        datas += copy_metadata(package)
    except Exception:
        pass

a = Analysis(
    [str(entry)],
    pathex=[str(repo_root)],
    binaries=[],
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
    [],
    exclude_binaries=True,
    name="renderdoc-mcp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="renderdoc-mcp",
)
