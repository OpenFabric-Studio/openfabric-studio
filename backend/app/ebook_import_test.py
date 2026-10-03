"""Bounded ebook extraction tests, using synthetic documents only."""
from __future__ import annotations

import importlib
import importlib.util
import asyncio
import io
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app import audiobooks, voice_profiles
import httpx
from fastapi import FastAPI
from app.api.routes_audiobooks import router


def epub_fixture(*, unsafe: bool = False, entities: bool = False, metadata_head: bool = False) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("META-INF/container.xml", '<!DOCTYPE container [<!ENTITY evil "unsafe">]><container>&evil;</container>' if entities else '<container><rootfiles><rootfile full-path="OEBPS/book.opf"/></rootfiles></container>')
        archive.writestr("OEBPS/book.opf", '<package xmlns:dc="urn:dc"><metadata><dc:title>Ficción</dc:title></metadata><manifest><item id="b" href="second.xhtml" media-type="application/xhtml+xml"/><item id="a" href="first.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="a"/><itemref idref="b"/></spine></package>')
        head = ('<head><title>Page metadata title</title><meta name="author" content="Metadata author"/>'
                '<style>p { color: black; }</style></head>') if metadata_head else ""
        archive.writestr("OEBPS/first.xhtml", f'<html>{head}<body><h1>One</h1><p>First <em>sentence</em>.</p><script>evil()</script><p>Second paragraph.</p><h1>Two</h1><p>Más texto.</p></body></html>')
        archive.writestr("OEBPS/second.xhtml", '<html><body><p>Last in reading order.</p></body></html>')
        if unsafe:
            archive.writestr("../escape", "bad")
    return output.getvalue()


def mobi_fixture(*, encrypted: bool = False) -> bytes:
    raw = bytearray(120)
    raw[60:68] = b"BOOKMOBI"
    struct.pack_into(">H", raw, 76, 1)
    struct.pack_into(">I", raw, 78, 86)
    struct.pack_into(">H", raw, 98, 2 if encrypted else 0)
    return bytes(raw)


class EbookExtractionTests(unittest.TestCase):
    def test_backend_exposes_bounded_extraction(self) -> None:
        self.assertIsNotNone(importlib.util.find_spec("app.ebook_import"), "ebook importer is missing")

    def test_spine_order_paragraphs_and_multiple_headings_preserve_text(self) -> None:
        importer = importlib.import_module("app.ebook_import")
        extracted = importer.extract_epub(epub_fixture())
        self.assertEqual(extracted.title, "Ficción")
        text = "\n".join(chapter.text for chapter in extracted.chapters)
        self.assertLess(text.index("First sentence."), text.index("Más texto."))
        self.assertLess(text.index("Más texto."), text.index("Last in reading order."))
        self.assertIn("First sentence.\n\nSecond paragraph.", text)
        self.assertNotIn("evil()", text)
        self.assertEqual(extracted.chapters[0].title, "One")
        self.assertEqual(extracted.chapters[1].title, "Two")

    def test_archive_paths_entities_and_expansion_are_rejected(self) -> None:
        importer = importlib.import_module("app.ebook_import")
        for data in (epub_fixture(unsafe=True), epub_fixture(entities=True)):
            with self.subTest(data=len(data)), self.assertRaises(importer.EbookImportError):
                importer.extract_epub(data)
        with patch.object(importer, "MAX_EXPANDED_BYTES", 10):
            with self.assertRaises(importer.EbookImportError) as error:
                importer.extract_epub(epub_fixture())
            self.assertEqual(error.exception.code, "ebook_too_large")

    def test_xhtml_head_metadata_never_becomes_a_narrated_chapter(self) -> None:
        importer = importlib.import_module("app.ebook_import")
        extracted = importer.extract_epub(epub_fixture(metadata_head=True))
        self.assertEqual([(chapter.title, chapter.text) for chapter in extracted.chapters], [
            ("One", "First sentence.\n\nSecond paragraph."),
            ("Two", "Más texto.\n\nLast in reading order."),
        ])

    def test_long_chapters_are_split_without_text_loss(self) -> None:
        importer = importlib.import_module("app.ebook_import")
        text = "word " * 5000
        chapters = importer.split_chapter("Long", text)
        self.assertGreater(len(chapters), 1)
        self.assertTrue(all(len(chapter.text) <= 20_000 for chapter in chapters))
        self.assertEqual("".join(chapter.text for chapter in chapters), text)


class EbookDraftTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.root_patch = patch.object(audiobooks, "BOOKS_ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def test_draft_revision_and_original_source_are_durable(self) -> None:
        importer = importlib.import_module("app.ebook_import")
        draft = importer.save_draft("original.mobi", b"source", importer.extract_epub(epub_fixture()))
        self.assertEqual(importer.get_draft(draft.id), draft)
        self.assertEqual(importer.source_path(draft.id).read_bytes(), b"source")
        body = importer.PatchEbookDraftRequest(title="Edited", chapters=draft.chapters, revision=draft.revision)
        updated = importer.patch_draft(draft.id, body)
        self.assertEqual(updated.title, "Edited")
        self.assertEqual(updated.revision, draft.revision + 1)
        with self.assertRaises(importer.EbookImportError) as error:
            importer.patch_draft(draft.id, body)
        self.assertEqual(error.exception.status, 409)

    def test_draft_quota_never_discards_an_existing_source(self) -> None:
        importer = importlib.import_module("app.ebook_import")
        self.assertTrue(hasattr(importer, "MAX_DRAFTS"), "draft storage has no bound")
        with patch.object(importer, "MAX_DRAFTS", 1):
            first = importer.save_draft("first.mobi", b"source", importer.extract_epub(epub_fixture()))
            with self.assertRaises(importer.EbookImportError) as error:
                importer.save_draft("second.mobi", b"other", importer.extract_epub(epub_fixture()))
            self.assertEqual(error.exception.code, "ebook_draft_limit")
            self.assertEqual(importer.source_path(first.id).read_bytes(), b"source")
            importer.delete_draft(first.id)
            self.assertEqual(importer.list_drafts(), [])

    def crash_delete(self, identifier: str, phase: str) -> None:
        program = """import os,sys
from pathlib import Path
from app import audiobooks,ebook_import
audiobooks.BOOKS_ROOT=Path(sys.argv[1])
if sys.argv[3]=='before_commit':
 original=Path.rename
 def interrupted(self,target):
  result=original(self,target)
  if Path(target).name.startswith('.deleted-'): os._exit(23)
  return result
 Path.rename=interrupted
else:
 original=ebook_import.shutil.rmtree
 def interrupted(path,*args,**kwargs):
  if Path(path).name.startswith('.deleted-'): os._exit(23)
  return original(path,*args,**kwargs)
 ebook_import.shutil.rmtree=interrupted
ebook_import.delete_draft(sys.argv[2])
"""
        result = subprocess.run([sys.executable, "-c", program, str(self.root), identifier, phase],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 23, result.stderr)

    def test_restart_restores_source_after_delete_crashes_before_commit_and_retry_succeeds(self) -> None:
        importer = importlib.import_module("app.ebook_import")
        draft = importer.save_draft("original.mobi", b"preserved source", importer.extract_epub(epub_fixture()))
        self.crash_delete(draft.id, "before_commit")
        self.assertEqual(len(list((self.root / "imports").glob(".deleted-*"))), 1)
        importer.start()
        self.assertEqual(importer.get_draft(draft.id), draft)
        self.assertEqual(importer.source_path(draft.id).read_bytes(), b"preserved source")
        importer.delete_draft(draft.id)
        self.assertEqual(importer.list_drafts(), [])
        self.assertEqual(list((self.root / "imports").iterdir()), [])

    def test_restart_finishes_source_cleanup_after_delete_commit(self) -> None:
        importer = importlib.import_module("app.ebook_import")
        draft = importer.save_draft("original.mobi", b"source", importer.extract_epub(epub_fixture()))
        self.crash_delete(draft.id, "after_commit")
        self.assertEqual(importer.list_drafts(), [])
        self.assertEqual(len(list((self.root / "imports").glob(".deleted-*"))), 1)
        importer.start()
        self.assertEqual(list((self.root / "imports").iterdir()), [])

    def test_ambiguous_interrupted_deletions_preserve_both_sources_and_report_storage_error(self) -> None:
        importer = importlib.import_module("app.ebook_import")
        draft = importer.save_draft("original.mobi", b"source", importer.extract_epub(epub_fixture()))
        original = self.root / "imports" / draft.id
        first = original.with_name(f".deleted-{draft.id}-{'a' * 32}")
        second = original.with_name(f".deleted-{draft.id}-{'b' * 32}")
        original.rename(first)
        shutil.copytree(first, second)
        with self.assertRaises(importer.EbookImportError) as error:
            importer.start()
        self.assertEqual(error.exception.code, "ebook_storage_unavailable")
        self.assertEqual(error.exception.status, 503)
        self.assertEqual((first / "source.mobi").read_bytes(), b"source")
        self.assertEqual((second / "source.mobi").read_bytes(), b"source")

    def test_interrupted_deletion_symlink_cannot_remove_an_outside_directory(self) -> None:
        importer = importlib.import_module("app.ebook_import")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "private").write_bytes(b"private")
        imports = self.root / "imports"
        imports.mkdir()
        (imports / f".deleted-{'a' * 32}-{'b' * 32}").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(importer.EbookImportError) as error:
            importer.start()
        self.assertEqual(error.exception.code, "ebook_storage_unavailable")
        self.assertEqual((outside / "private").read_bytes(), b"private")

    def test_missing_draft_catalog_cannot_authorize_staged_source_cleanup(self) -> None:
        importer = importlib.import_module("app.ebook_import")
        imports = self.root / "imports"
        imports.mkdir()
        staged = imports / f".deleted-{'a' * 32}-{'b' * 32}"
        staged.mkdir()
        (staged / "source.mobi").write_bytes(b"preserved source")
        with self.assertRaises(importer.EbookImportError) as error:
            importer.start()
        self.assertEqual(error.exception.code, "ebook_storage_unavailable")
        self.assertEqual((staged / "source.mobi").read_bytes(), b"preserved source")


class EbookImportApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.root_patch = patch.object(audiobooks, "BOOKS_ROOT", self.root / "books")
        self.root_patch.start()
        self.profiles_patch = patch.object(voice_profiles, "PROFILES_ROOT", self.root / "profiles")
        self.profiles_patch.start()
        app = FastAPI()
        app.include_router(router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1")
        importer = importlib.import_module("app.ebook_import")
        importer.start()
        await audiobooks.start()

    async def asyncTearDown(self) -> None:
        importer = importlib.import_module("app.ebook_import")
        await importer.shutdown()
        await audiobooks.shutdown()
        await self.client.aclose()
        self.root_patch.stop()
        self.profiles_patch.stop()
        self.temporary.cleanup()

    async def test_missing_converter_and_encrypted_input_have_stable_errors(self) -> None:
        importer = importlib.import_module("app.ebook_import")
        with patch.object(importer, "converter_path", return_value=None):
            response = await self.client.post("/api/audiobooks/imports", files={"file": ("book.mobi", mobi_fixture())})
            self.assertEqual(response.status_code, 503, response.text)
            self.assertEqual(response.json()["detail"], "ebook_converter_missing")
            response = await self.client.post("/api/audiobooks/imports", files={"file": ("book.mobi", mobi_fixture(encrypted=True))})
            self.assertEqual(response.status_code, 400, response.text)
            self.assertEqual(response.json()["detail"], "ebook_encrypted")

    async def test_foreign_origin_cannot_upload_or_mutate_local_books(self) -> None:
        response = await self.client.post("/api/audiobooks/imports", files={"file": ("book.mobi", mobi_fixture())}, headers={"Origin": "https://attacker.example"})
        self.assertEqual(response.status_code, 403, response.text)
        response = await self.client.post("/api/audiobooks/" + "a" * 32 + "/cancel", headers={"Origin": "https://attacker.example"})
        self.assertEqual(response.status_code, 403, response.text)

    async def test_import_review_reload_and_source_containment(self) -> None:
        importer = importlib.import_module("app.ebook_import")
        fixture = self.root / "fixture.epub"
        fixture.write_bytes(epub_fixture())
        converter = self.root / "converter"
        converter.write_text(f"#!{sys.executable}\nimport os,shutil,sys\nfrom pathlib import Path\nfor key in ('CALIBRE_CONFIG_DIRECTORY','CALIBRE_TEMP_DIR','CALIBRE_CACHE_DIRECTORY'):\n assert Path(os.environ[key]).is_dir() and Path(os.environ[key]).resolve().parent == Path.cwd()\nshutil.copyfile({str(fixture)!r}, sys.argv[2])\n")
        converter.chmod(0o700)
        with patch.object(importer, "converter_path", return_value=converter):
            response = await self.client.post("/api/audiobooks/imports", files={"file": ("book.mobi", mobi_fixture())})
        self.assertEqual(response.status_code, 200, response.text)
        draft = response.json()
        identifier = draft["id"]
        loaded = await self.client.get(f"/api/audiobooks/imports/{identifier}")
        self.assertEqual(loaded.json(), draft)
        source = await self.client.get(f"/api/audiobooks/imports/{identifier}/source")
        self.assertEqual(source.content, mobi_fixture())
        edited = await self.client.patch(f"/api/audiobooks/imports/{identifier}", json={"title": "Reviewed", "chapters": draft["chapters"], "revision": 1})
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertEqual(edited.json()["revision"], 2)
        conflict = await self.client.patch(f"/api/audiobooks/imports/{identifier}", json={"title": "Stale", "chapters": draft["chapters"], "revision": 1})
        self.assertEqual(conflict.status_code, 409)
        private = self.root / "private"
        private.write_bytes(b"secret")
        source_path = importer.source_path(identifier)
        source_path.unlink()
        source_path.symlink_to(private)
        source = await self.client.get(f"/api/audiobooks/imports/{identifier}/source")
        self.assertEqual(source.status_code, 404)

    async def test_cancelled_converter_reaps_descendant_and_does_not_save_a_draft(self) -> None:
        importer = importlib.import_module("app.ebook_import")
        marker = self.root / "child.pid"
        converter = self.root / "slow-converter"
        converter.write_text(f"#!{sys.executable}\nimport subprocess,sys,time\nfrom pathlib import Path\np=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'])\nPath({str(marker)!r}).write_text(str(p.pid))\ntime.sleep(120)\n")
        converter.chmod(0o700)
        with patch.object(importer, "converter_path", return_value=converter):
            task = asyncio.create_task(importer.import_mobi("book.mobi", mobi_fixture()))
            async with asyncio.timeout(5):
                while not marker.exists():
                    if task.done():
                        await task
                    await asyncio.sleep(0.01)
            child = int(marker.read_text())
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        async with asyncio.timeout(5):
            while True:
                try:
                    os.kill(child, 0)
                except ProcessLookupError:
                    break
                await asyncio.sleep(0.01)
        self.assertEqual(importer.list_drafts(), [])

    async def test_converter_workspace_growth_is_stopped_before_completion(self) -> None:
        importer = importlib.import_module("app.ebook_import")
        self.assertTrue(hasattr(importer, "MAX_WORKSPACE_BYTES"), "running converter disk growth is unbounded")
        converter = self.root / "growing-converter"
        converter.write_text(f"#!{sys.executable}\nimport sys,time\nfrom pathlib import Path\nPath('expanded').write_bytes(b'x'*65536)\ntime.sleep(120)\n")
        converter.chmod(0o700)
        with patch.object(importer, "converter_path", return_value=converter), patch.object(importer, "MAX_WORKSPACE_BYTES", 4096):
            with self.assertRaises(importer.EbookImportError) as error:
                await importer.import_mobi("book.mobi", mobi_fixture())
            self.assertEqual(error.exception.code, "ebook_too_large")
        self.assertEqual(importer.list_drafts(), [])

    async def test_review_selection_and_source_provenance_survive_narration(self) -> None:
        importer = importlib.import_module("app.ebook_import")
        draft = importer.save_draft("original.mobi", mobi_fixture(), importer.extract_epub(epub_fixture()))
        chapters = [chapter.model_dump() for chapter in draft.chapters]
        chapters[0]["included"] = False
        edited = await self.client.patch(f"/api/audiobooks/imports/{draft.id}", json={"title": "Reviewed", "chapters": chapters, "revision": 1})
        self.assertEqual(edited.status_code, 200, edited.text)
        profile = voice_profiles.create_profile(name="Reader", consent_confirmed=True, audio_bytes=b"RIFF....WAVE", filename="ref.wav", notes="Reference")
        with patch.dict(os.environ, {"OPENFABRIC_AUDIOBOOK_SYNC": "1", "OPENFABRIC_SPEECH_CLONE_MOCK": "1"}):
            stale = await self.client.post(f"/api/audiobooks/imports/{draft.id}/create", json={"profile_id": profile.id, "revision": 1})
            self.assertEqual(stale.status_code, 409)
            created = await self.client.post(f"/api/audiobooks/imports/{draft.id}/create", json={"profile_id": profile.id, "revision": 2})
            self.assertEqual(created.status_code, 200, created.text)
        book = created.json()["book"]
        self.assertEqual(book["source_import_id"], draft.id)
        self.assertEqual(book["chapter_count"], 1)
        self.assertEqual(importer.source_path(draft.id).read_bytes(), mobi_fixture())
        summaries = (await self.client.get("/api/audiobooks/imports")).json()["drafts"]
        self.assertNotIn("chapters", summaries[0])
        removed = await self.client.delete(f"/api/audiobooks/imports/{draft.id}")
        self.assertEqual(removed.status_code, 409)

    async def test_source_deleted_between_review_read_and_book_insert_cannot_create_book(self) -> None:
        importer = importlib.import_module("app.ebook_import")
        draft = importer.save_draft("original.mobi", mobi_fixture(), importer.extract_epub(epub_fixture()))
        profile = voice_profiles.create_profile(name="Reader", consent_confirmed=True, audio_bytes=b"RIFF....WAVE", filename="ref.wav", notes="Reference")
        original = voice_profiles.get_profile

        def remove_source(identifier: str) -> voice_profiles.VoiceProfile:
            importer.delete_draft(draft.id)
            return original(identifier)

        with patch.object(voice_profiles, "get_profile", side_effect=remove_source):
            response = await self.client.post(f"/api/audiobooks/imports/{draft.id}/create", json={"profile_id": profile.id, "revision": 1})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"], "ebook_draft_conflict")
        self.assertEqual(audiobooks.list_books(), [])
