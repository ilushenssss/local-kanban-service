import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile


class DataBlob(ctypes.Structure):
    _fields_ = (('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte)))


def dpapi(data, *, encrypt):
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    fn = crypt.CryptProtectData if encrypt else crypt.CryptUnprotectData
    fn.argtypes = (ctypes.POINTER(DataBlob), ctypes.c_wchar_p if encrypt else ctypes.c_void_p,
                   ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                   ctypes.POINTER(DataBlob))
    fn.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = (ctypes.c_void_p,)
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = DataBlob(len(data), buffer)
    target = DataBlob()
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(ctypes.cast(target.data, ctypes.c_void_p))


class PasswordStore:
    def __init__(self):
        local = os.environ.get('LOCALAPPDATA') if sys.platform == 'win32' else None
        root = Path(local) if local else None
        self.folder = root / 'TeamSync' / 'credentials' if root is not None and root.is_absolute() else None

    @property
    def available(self):
        return self.folder is not None

    @staticmethod
    def key(value):
        return hashlib.sha256(value.encode('utf-8')).hexdigest()

    def path(self, service, url, login):
        return self.folder / f'{service}-{self.key(url)}-{self.key(login)}.dat'

    def load(self, service, url, login=None):
        if not self.available:
            return None
        try:
            paths = ([self.path(service, url, login)] if login is not None else
                     sorted(self.folder.glob(f'{service}-{self.key(url)}-*.dat'),
                            key=lambda path: path.stat().st_mtime_ns, reverse=True))
        except OSError:
            return None
        for path in paths:
            try:
                with path.open('rb') as file:
                    data = file.read(65_537)
                if not 0 < len(data) <= 65_536:
                    continue
                row = json.loads(dpapi(data, encrypt=False))
                if (row['service'] == service and row['url'] == url
                        and isinstance(row['login'], str) and row['login']
                        and isinstance(row['password'], str) and row['password']
                        and self.path(service, url, row['login']) == path):
                    return row['login'], row['password']
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return None

    def save(self, service, url, login, password):
        if not self.available:
            return
        plain = json.dumps({'service': service, 'url': url, 'login': login, 'password': password},
                           ensure_ascii=False).encode('utf-8')
        data = dpapi(plain, encrypt=True)
        if len(data) > 65_536:
            raise OSError('Сохранённые учётные данные превышают допустимый размер.')
        self.folder.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.folder, delete=False) as file:
                temporary = Path(file.name)
                file.write(data)
            os.replace(str(temporary), str(self.path(service, url, login)))
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    def forget(self, service):
        if self.available:
            for path in self.folder.glob(f'{service}-*.dat'):
                path.unlink(missing_ok=True)

    def forget_rejected(self, service, url, login, password):
        if self.load(service, url, login) == (login, password):
            self.path(service, url, login).unlink(missing_ok=True)
