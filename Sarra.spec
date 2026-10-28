# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import os

from PyInstaller.building.splash import Splash
from PyInstaller.utils.hooks import collect_submodules


root = Path(SPECPATH)
actions_dir = root / 'actions'
action_datas = [(str(path), 'actions') for path in sorted(actions_dir.glob('*.py'))]
dashboard_static = root / 'dashboard' / 'static'
dashboard_datas = [
    (str(path), 'dashboard/static')
    for path in sorted(dashboard_static.iterdir())
    if path.is_file()
]

# The action loader imports action files from disk at runtime, so PyInstaller
# cannot discover their Playwright imports by following main.py's import graph.
# Bundle its Python modules and the Node driver payload they launch.
import playwright

playwright_dir = Path(playwright.__file__).parent
playwright_driver = playwright_dir / 'driver'
playwright_datas = [
    (
        str(path),
        # PyInstaller's datas destination is a directory, not the output filename.
        str(Path('playwright') / path.relative_to(playwright_dir).parent),
    )
    for path in sorted((playwright_driver / 'package').rglob('*'))
    if path.is_file()
]
driver_name = 'node.exe' if os.name == 'nt' else 'node'
playwright_binaries = [
    (str(playwright_driver / driver_name), 'playwright/driver'),
]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=playwright_binaries,
    datas=action_datas + dashboard_datas + playwright_datas + [
        (str(root / 'assets'), 'assets'),
        (str(root / 'config' / 'avatar_camera.json'), 'config'),
        (str(root / 'config' / 'avatar_lipsync.json'), 'config'),
        (str(root / 'config' / 'avatar_window.json'), 'config'),
    ],
    hiddenimports=['config', 'pyautogui'] + collect_submodules('playwright'),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
class SarraSplash(Splash):
    def generate_script(self):
        script = super().generate_script()
        script += """
set sarra_spinner_phase 0
set sarra_spinner [.root.canvas create arc 286 10 474 178 \\
    -start 0 -extent 48 -style arc -outline #82f2ff -width 4]
proc animate_sarra_spinner {} {
    global sarra_spinner_phase sarra_spinner
    set sarra_spinner_phase [expr {($sarra_spinner_phase + 12) % 360}]
    .root.canvas itemconfigure $sarra_spinner -start $sarra_spinner_phase
    after 40 animate_sarra_spinner
}
after 40 animate_sarra_spinner
"""
        Path(self.script_name).write_text(script, encoding='utf-8')
        return script


splash = SarraSplash(
    str(root / 'assets' / 'sarra_splash.png'),
    binaries=a.binaries,
    datas=a.datas,
    always_on_top=True,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    splash,
    a.binaries,
    a.datas,
    [],
    name='Sarra',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
