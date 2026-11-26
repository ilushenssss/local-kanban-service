import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from kanban.private_chat import HEADER, PrivateChatTransport
from kanban.store import Store


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
                with patch.object(recipient, 'receive', wraps=recipient.receive) as receive:
                    sender.send(bob_id, message_id, message)
                    sender.send(bob_id, message_id, message)
                self.assertEqual(receive.call_count, 2)
                self.assertEqual(receive.call_args.args[0][HEADER.size:], message_id + message.encode('utf-8'))
                self.assertEqual(inbox, [(alice.profile()['id'], 'alice', message)])
                self.assertNotIn('public', alice.private_chat_peer(bob_id)[0])
                self.assertNotIn(message.encode('utf-8'), alice.db.read_bytes())
                alice.set_user_restricted(bob_id, True)
                with self.assertRaisesRegex(ValueError, 'недоступен'):
                    sender.send(bob_id, uuid4().bytes, 'Доступ закрыт')
            finally:
                recipient.stop()
                sender.stop()
