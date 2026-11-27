from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from kanban.credentials import PasswordStore, dpapi


class CredentialsTest(unittest.TestCase):
    def test_saved_passwords_are_scoped_and_removal_is_selective(self):
        with tempfile.TemporaryDirectory() as folder:
            vault = PasswordStore()
            vault.folder = Path(folder)
            with patch('kanban.credentials.dpapi',
                       side_effect=lambda data, **kwargs: bytes(value ^ 165 for value in data)):
                vault.save('redmine', 'https://redmine.test', 'first', 'пароль один')
                vault.save('redmine', 'https://redmine.test', 'second', 'пароль два')
                vault.save('bugzilla', 'https://bugs.test', 'first', 'пароль три')
                self.assertEqual(vault.load('redmine', 'https://redmine.test', 'first'), ('first', 'пароль один'))
                self.assertIsNone(vault.load('redmine', 'https://another.test', 'first'))
                self.assertIsNone(vault.load('bugzilla', 'https://bugs.test', 'second'))
                for path in vault.folder.iterdir():
                    self.assertNotIn('пароль'.encode(), path.read_bytes())
                self.assertEqual(vault.load('redmine', 'https://redmine.test'), ('second', 'пароль два'))
                vault.forget_rejected('redmine', 'https://redmine.test', 'second', 'устаревший')
                self.assertEqual(vault.load('redmine', 'https://redmine.test', 'second'), ('second', 'пароль два'))
                vault.forget_rejected('redmine', 'https://redmine.test', 'second', 'пароль два')
                self.assertIsNone(vault.load('redmine', 'https://redmine.test', 'second'))
                vault.forget('redmine')
                self.assertIsNone(vault.load('redmine', 'https://redmine.test'))
                self.assertEqual(vault.load('bugzilla', 'https://bugs.test'), ('first', 'пароль три'))

    @unittest.skipUnless(sys.platform == 'win32', 'Windows DPAPI')
    def test_actual_dpapi_round_trip(self):
        data = 'тестовый пароль'.encode()
        encrypted = dpapi(data, encrypt=True)
        self.assertNotIn(data, encrypted)
        self.assertEqual(dpapi(encrypted, encrypt=False), data)
