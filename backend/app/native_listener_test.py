"""Platform listener identity parsers fail closed on incomplete evidence."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.orchestrator.listener import _linux_listener_pids, _parse_pids, listener_pids


class NativeListenerTests(unittest.TestCase):
    def test_lsof_always_emits_file_descriptor_fields_with_the_pids(self) -> None:
        self.assertEqual(_parse_pids(b'p123\nf3\np456\nf10\n', 'p'), frozenset({123, 456}))

    def test_identity_parser_rejects_unknown_and_invalid_pid_fields(self) -> None:
        for output in (b'p0\n', b'p-1\n', b'p12\nn127.0.0.1\n', b'p123\n' * 20000):
            with self.subTest(output=output[:32]), self.assertRaises(OSError):
                _parse_pids(output, 'p')
        self.assertEqual(_parse_pids(b'123\r\n456\r\n'), frozenset({123, 456}))

    def test_linux_listener_requires_every_socket_inode_to_have_a_pid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'net').mkdir()
            (root / 'net' / 'tcp').write_text('header\n 0: 0100007F:2328 00000000:0000 0A 0 0 0 0 0 42\n')
            (root / '123' / 'fd').mkdir(parents=True)
            os.symlink('socket:[42]', root / '123' / 'fd' / '3')
            self.assertEqual(_linux_listener_pids(9000, root), frozenset({123}))
            (root / '123' / 'fd' / '3').unlink()
            with self.assertRaises(OSError):
                _linux_listener_pids(9000, root)
            self.assertEqual(_linux_listener_pids(9001, root), frozenset())


class UnsupportedListenerTests(unittest.IsolatedAsyncioTestCase):
    async def test_unavailable_os_ownership_is_not_guessed(self) -> None:
        with patch('app.orchestrator.listener.sys.platform', 'unknown'):
            with self.assertRaises(OSError):
                await listener_pids(9000)

    async def test_listener_query_rejects_output_overflow(self) -> None:
        from app.orchestrator.listener import _query_pids
        with self.assertRaises(OSError):
            await _query_pids([sys.executable, '-c', "import sys;sys.stdout.write('123\\n'*20000)"], '', False)

    async def test_successful_listener_query_preserves_pid_decoding(self) -> None:
        from app.orchestrator.listener import _query_pids
        self.assertEqual(await _query_pids([sys.executable, '-c', "print('123');print('456')"], '', False), frozenset({123, 456}))
