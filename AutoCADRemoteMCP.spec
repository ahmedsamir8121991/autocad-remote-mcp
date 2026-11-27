# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import copy_metadata

datas = [('C:/autocad-remote-mcp/.venv/Lib/site-packages/templates', 'templates')]
hiddenimports = ['acad_server', 'acad_pro', 'acad_skills', 'acad_bridge', 'acad_search', 'acad_crypto', 'acad_template', 'server', 'config', 'security', 'security_lisp', 'policy', 'http_guard', 'version', 'mcp.types', 'win32timezone']
datas += collect_data_files('engineering')
datas += collect_data_files('discovery')
datas += collect_data_files('fastmcp')
datas += collect_data_files('ezdxf')
datas += copy_metadata('fastmcp')
datas += copy_metadata('fastmcp-slim')
datas += copy_metadata('mcp')
datas += copy_metadata('autocad-mcp-pro')
hiddenimports += collect_submodules('backends')
hiddenimports += collect_submodules('engineering')
hiddenimports += collect_submodules('discovery')
hiddenimports += collect_submodules('fastmcp.server')
hiddenimports += collect_submodules('fastmcp.client')
hiddenimports += collect_submodules('fastmcp.tools')
hiddenimports += collect_submodules('fastmcp.utilities')
hiddenimports += collect_submodules('mcp.server')
hiddenimports += collect_submodules('mcp.client')
hiddenimports += collect_submodules('mcp.shared')


a = Analysis(
    ['src/launcher.py'],
    pathex=['src'],
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
    a.binaries,
    a.datas,
    [],
    name='AutoCADRemoteMCP',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
