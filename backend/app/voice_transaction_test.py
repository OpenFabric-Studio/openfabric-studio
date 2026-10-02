"""Concurrent recovery never replaces newer voice metadata with a stale read."""
from __future__ import annotations

import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from app import voice_build as voice
from app.atomic_files import JsonObject, document_lock, read_object, write_object


class VoiceTransactionTests(unittest.TestCase):
    def test_interrupted_build_reconciliation_preserves_a_concurrent_edit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = directory / "voice.json"
            write_object(path, {"id": "a" * 32, "name": "original", "status": "training"})
            read = threading.Event()
            editing = threading.Event()
            edited = threading.Event()
            original = voice.reconcile_interrupted

            def reconcile(meta: JsonObject) -> JsonObject | None:
                read.set()
                self.assertTrue(editing.wait(2))
                edited.wait(0.2)
                return original(meta)

            def edit() -> None:
                self.assertTrue(read.wait(2))
                editing.set()
                with document_lock(path):
                    meta = read_object(path)
                    meta["name"] = "updated"
                    write_object(path, meta)
                edited.set()

            with patch.dict(voice._builds, {}, clear=True), patch.object(voice, "reconcile_interrupted", side_effect=reconcile):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    reader = pool.submit(voice.public_from_path, directory)
                    writer = pool.submit(edit)
                    reader.result(timeout=3)
                    writer.result(timeout=3)
            self.assertEqual(read_object(path)["name"], "updated")

    def test_apply_recovery_preserves_a_concurrent_metadata_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "_apply"
            directory.mkdir()
            path = directory / "1.json"
            write_object(path, {"status": "running"})
            read = threading.Event()
            editing = threading.Event()
            edited = threading.Event()
            original = Path.read_text

            def read_text(file: Path, *args: object, **kwargs: object) -> str:
                content = original(file, encoding="utf-8")
                if file == path and threading.current_thread().name.endswith("_0"):
                    read.set()
                    self.assertTrue(editing.wait(2))
                    edited.wait(0.2)
                return content

            def edit() -> None:
                self.assertTrue(read.wait(2))
                editing.set()
                with document_lock(path):
                    meta = read_object(path)
                    meta["marker"] = "updated"
                    write_object(path, meta)
                edited.set()

            with patch.object(voice, "VOICES_DIR", root), patch.dict(voice._applies, {}, clear=True), patch.object(Path, "read_text", read_text):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    reader = pool.submit(voice.apply_status, 1)
                    writer = pool.submit(edit)
                    reader.result(timeout=3)
                    writer.result(timeout=3)
            self.assertEqual(read_object(path).get("marker"), "updated")

    def test_number_rejects_objects_and_non_finite_values(self) -> None:
        for value in (object(), None, {}, [], True, "nan", "inf", -1):
            with self.subTest(value=value):
                self.assertEqual(voice._number(value), 0.0)
        self.assertEqual(voice._number("12.5"), 12.5)
