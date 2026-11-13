"""Create an offline release from a prepared runtime; no network requests."""
import argparse
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent.parent


def package(destination):
    destination = Path(destination)
    if not (ROOT / 'environment/python/python.exe').is_file():
        raise SystemExit('Prepare environment/python first with tools/build_windows.py')
    if destination.exists():
        raise SystemExit('Destination already exists; choose a new archive name')
    paths = []
    for directory in ['kanban', 'tools', 'tests', 'docs', 'requirements', 'environment']:
        paths.extend(p for p in (ROOT / directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc' and 'screenshots' not in p.parts)
    paths.extend(ROOT / name for name in ['main.pyw', 'Create-shortcut.vbs', 'kanban.ini.example', 'README.md'])
    with zipfile.ZipFile(str(destination), 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(paths):
            archive.write(str(path), 'Kanban/' + path.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(str(destination)) as archive:
        bad = archive.testzip()
        if bad:
            raise IOError('Archive verification failed: ' + bad)
    print(destination, destination.stat().st_size, 'bytes; archive CRC verified')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination')
    package(parser.parse_args().destination)
