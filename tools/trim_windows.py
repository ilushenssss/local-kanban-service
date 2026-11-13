"""Retain only the Python/Qt runtime used by TeamSync. Never run at application startup."""
from pathlib import Path
import hashlib
import json
import shutil

ROOT = Path(__file__).resolve().parent.parent
KEEP_PYQT = {'__init__.py', 'QtCore.pyd', 'QtGui.pyd', 'QtWidgets.pyd', 'QtTest.pyd', 'sip.cp38-win_amd64.pyd', 'Qt5'}
KEEP_BIN = {'Qt5Core.dll', 'Qt5Gui.dll', 'Qt5Widgets.dll', 'Qt5Test.dll', 'libEGL.dll', 'libGLESv2.dll', 'd3dcompiler_47.dll', 'concrt140.dll', 'msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll', 'vcruntime140.dll', 'vcruntime140_1.dll'}
KEEP_PLUGINS = {'platforms': {'qwindows.dll', 'qminimal.dll', 'qoffscreen.dll'}, 'styles': {'qwindowsvistastyle.dll'}, 'imageformats': {'qgif.dll', 'qico.dll', 'qjpeg.dll'}}


def remove(path):
    if path.is_dir():
        shutil.rmtree(str(path))
    elif path.exists():
        path.unlink()


def keep_only(folder, names):
    for path in folder.iterdir():
        if path.name not in names:
            remove(path)


def trim(target=None):
    target = Path(target or ROOT / 'environment/python')
    if not (target / 'pythonw.exe').is_file():
        raise ValueError('Expected prepared Windows Python runtime')
    for name in ['include', 'libs', 'Tools']:
        remove(target / name)
    site = target / 'Lib/site-packages'
    remove(site / 'bin')
    qt = site / 'PyQt5/Qt5'
    keep_only(site / 'PyQt5', KEEP_PYQT)
    keep_only(qt, {'bin', 'plugins', 'translations'})
    keep_only(qt / 'bin', KEEP_BIN)
    keep_only(qt / 'plugins', set(KEEP_PLUGINS))
    for name, files in KEEP_PLUGINS.items():
        keep_only(qt / 'plugins' / name, files)
    keep_only(qt / 'translations', {'qtbase_ru.qm'})
    for path in list(target.rglob('__pycache__')):
        remove(path)
    manifest = {p.relative_to(target).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(target.rglob('*')) if p.is_file()}
    (target.parent / 'runtime-manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print('Trimmed runtime:', target)


if __name__ == '__main__':
    trim()
