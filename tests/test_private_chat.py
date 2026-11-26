import tempfile
import unittest
from importlib.util import find_spec
from uuid import uuid4

from kanban.private_chat import PrivateChatTransport
from kanban.store import Store


@unittest.skipUnless(find_spec('cryptography'), 'cryptography is required for private chats')
class PrivateChatTest(unittest.TestCase):
    def test_delivery_is_deduplicated_and_requires_an_active_employee(self):
        with tempfile.TemporaryDirectory() as folder:
            alice = Store(folder, 'alice')
            alice.initialize_admin('alice')
            bob_id = alice.save_user('bob', 'Борис')
            bob = Store(folder, 'bob')
            inbox = []
            sender = PrivateChatTransport(alice, lambda *args: None)
            recipient = PrivateChatTransport(bob, lambda _, user, name, text: inbox.append((user, name, text)))
            sender.start()
            try:
                with self.assertRaisesRegex(ValueError, 'недоступен'):
                    sender.send(bob_id, uuid4().bytes, 'Нет получателя')
                recipient.start()
                message = 'Сообщение только в памяти'
                message_id = uuid4().bytes
                sender.send(bob_id, message_id, message)
                sender.send(bob_id, message_id, message)
                self.assertEqual(inbox, [(alice.profile()['id'], 'alice', message)])
                self.assertNotIn(message.encode('utf-8'), alice.db.read_bytes())
                alice.set_user_restricted(bob_id, True)
                with self.assertRaisesRegex(ValueError, 'недоступен'):
                    sender.send(bob_id, uuid4().bytes, 'Доступ закрыт')
            finally:
                recipient.stop()
                sender.stop()
