"""Online preparation only; never invoked by the application. Python 3.8 x64 bundle."""
import hashlib
import io
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent
PYTHON_HASH = 'c63a2fc8fc62612b5abd391fda99ae1d90bad42ed8a9e99b1bd81c7ffdb4fefa'
WHEELS = {
    'PyQt5-5.15.11-cp38-abi3-win_amd64.whl': 'bdde598a3bb95022131a5c9ea62e0a96bd6fb28932cc1619fd7ba211531b7517',
    'PyQt5_Qt5-5.15.2-py3-none-win_amd64.whl': '750b78e4dba6bdf1607febedc08738e318ea09e9b10aea9ff0d73073f11f6962',
    'PyQt5_sip-12.15.0-cp38-cp38-win_amd64.whl': 'f600ae6f03e4bff91153c0dc7ebe52f90bd2b6afda58fd580e6990b3b951adc0',
    'certifi-2026.11.20-py3-none-any.whl': '7422df25243767b9877031a2248cb14d09ddac9beee846f4cef3d5c9191d488f',
    'charset_normalizer-3.5.2-cp37-abi3-win_amd64.whl': '9f56f72050826f63dcee7a7f55b0a77168cb3bfc553fd405e7f8f9ece75a4036',
    'idna-3.15-py3-none-any.whl': '048adeaf8c2d788c40fee287673ccaa74c24ffd8dcf09ffa555a2fbb59f10ac8',
    'python_redmine-2.5.0-py3-none-any.whl': '4b9c1dbcf46f96bed963ab48eab432f06c7ae342df9751d04cb54d268e2e8055',
    'requests-2.32.4-py3-none-any.whl': '27babd3cda2a6d50b30443204ee89830707d396671944c998b5975b031ac2b2c',
    'urllib3-2.2.3-py3-none-any.whl': 'ca899ca043dcb1bafa3e262d73aa25c465bfb49e0bd9dd5d59f1d0acba2f8fac',
}


def build():
    target = ROOT / 'environment' / 'python'
    if target.exists():
        raise SystemExit('environment/python already exists. Preserve or remove it explicitly before rebuilding.')
    wheels = ROOT / 'wheelhouse'
    wheels.mkdir(exist_ok=True)
    subprocess.check_call([
        sys.executable, '-m', 'pip', 'download', '--only-binary=:all:', '--no-deps',
        '--platform', 'win_amd64', '--python-version', '3.8', '--implementation', 'cp',
        '--abi', 'cp38', '--abi', 'abi3', '-d', str(wheels),
        '-r', str(ROOT / 'requirements' / 'runtime.txt'),
        '-r', str(ROOT / 'requirements' / 'redmine.txt'),
    ])
    for name, expected in WHEELS.items():
        if hashlib.sha256((wheels / name).read_bytes()).hexdigest() != expected:
            raise SystemExit('Wheel checksum mismatch: ' + name)
    with urllib.request.urlopen('https://api.nuget.org/v3-flatcontainer/python/3.8.10/python.3.8.10.nupkg', timeout=120) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != PYTHON_HASH:
        raise SystemExit('Python checksum mismatch')
    target.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            if not info.filename.startswith('tools/') or info.is_dir():
                continue
            rel = Path(info.filename[6:])
            if '..' in rel.parts or rel.is_absolute():
                raise ValueError('Unsafe archive path')
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, dest.open('wb') as dst:
                shutil.copyfileobj(src, dst)
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--no-index', '--no-deps', '--no-compile', '--target', str(target / 'Lib' / 'site-packages'), '--platform', 'win_amd64', '--python-version', '3.8', '--only-binary=:all:'] + [str(wheels / name) for name in WHEELS])
    (target / 'python38._pth').write_text('DLLs\nLib\n.\n..\\..\nimport site\n', encoding='ascii')
    (ROOT / 'environment' / 'SHA256SUMS').write_text('\n'.join(digest + '  ' + name for name, digest in WHEELS.items()) + '\n' + PYTHON_HASH + '  python.3.8.10.nupkg\n', encoding='ascii')
    from trim_windows import trim
    trim(target)
    print('Prepared isolated Windows bundle:', target)


if __name__ == '__main__':
    build()
