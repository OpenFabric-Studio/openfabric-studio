from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app import audiobooks, ebook_import as imports


def docx(body: str, *, extra: tuple[str, str] | None = None) -> bytes:
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w") as archive:
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + body + '</w:body></w:document>')
        if extra:
            archive.writestr(*extra)
    return result.getvalue()


class DocumentImportTests(unittest.TestCase):
    def test_docx_preserves_paragraph_order_headings_and_warns_about_unsupported_content(self) -> None:
        raw = docx('<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Opening</w:t></w:r></w:p>'
                   '<w:p><w:r><w:t>Hello </w:t></w:r><w:r><w:t>world.</w:t></w:r></w:p>'
                   '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Table text.</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
                   '<w:p><w:r><w:drawing/></w:r></w:p>')
        value = imports.extract_uploaded("story.docx", raw)
        self.assertEqual(value.chapters[0].title, "Opening")
        self.assertEqual(value.chapters[0].text, "Hello world.\n\nTable text.")
        self.assertIn("unsupported_content", [warning.code for warning in value.warnings])

    def test_docx_rejects_paths_entities_duplicate_members_and_expansion(self) -> None:
        for raw in (docx("<w:p/>", extra=("../private", "secret")),
                    docx("<w:p/>", extra=("word/document.xml", "duplicate")),
                    docx("<!DOCTYPE evil [<!ENTITY a 'expand'>]><w:p/>")):
            with self.subTest(raw=len(raw)), self.assertRaises(imports.EbookImportError):
                imports.extract_uploaded("story.docx", raw)
        with patch.object(imports, "MAX_EXPANDED_BYTES", 10):
            with self.assertRaises(imports.EbookImportError):
                imports.extract_uploaded("story.docx", docx("<w:p/>"))

    def test_docx_textboxes_and_deleted_changes_do_not_duplicate_or_restore_hidden_text(self) -> None:
        raw = docx('<w:p><w:r><w:t>Visible prose.</w:t><w:drawing><w:txbxContent><w:p><w:r><w:t>Textbox.</w:t></w:r></w:p></w:txbxContent></w:drawing></w:r>'
                   '<w:del><w:r><w:t>Deleted prose.</w:t></w:r></w:del></w:p>')
        value = imports.extract_uploaded("story.docx", raw)
        self.assertEqual(value.chapters[0].text, "Visible prose.")
        self.assertIn("unsupported_content", [warning.code for warning in value.warnings])

    def test_long_subtitles_preserve_cues_speakers_source_times_and_overlap_warning(self) -> None:
        raw = "\n\n".join(f"id-{index}\n00:00:00,000 --> 00:00:02,000\nAlice: Sentence {index}." for index in range(3000))
        value = imports.extract_uploaded("scene.srt", raw.encode())
        cues = [cue for chapter in value.chapters for cue in chapter.source_cues]
        self.assertEqual(len(cues), 3000)
        self.assertEqual([cue.cue_id for cue in cues], [f"id-{index}" for index in range(3000)])
        self.assertEqual(cues[-1].order, 2999)
        self.assertEqual(cues[0].speaker, "Alice")
        self.assertEqual((cues[0].start_ms, cues[0].end_ms), (0, 2000))
        self.assertEqual(cues[0].text, "Sentence 0.")
        self.assertGreater(len(value.chapters), 1)
        self.assertTrue(all(len(chapter.text) <= 20000 for chapter in value.chapters))
        self.assertIn("subtitle_overlap", [warning.code for warning in value.warnings])

    def test_vtt_preserves_named_voice_and_identifier_without_narrating_markup(self) -> None:
        raw = b"WEBVTT\n\noriginal-cue\n00:01.250 --> 00:03.500 align:start\n<v Alice>Hello <i>world</i>.</v>\n"
        value = imports.extract_uploaded("scene.vtt", raw)
        cue = value.chapters[0].source_cues[0]
        self.assertEqual((cue.cue_id, cue.speaker, cue.start_ms, cue.end_ms, cue.text), ("original-cue", "Alice", 1250, 3500, "Hello world."))
        self.assertEqual(value.chapters[0].text, "Alice: Hello world.")

    def test_vtt_header_metadata_and_note_prefixed_ids_do_not_drop_real_cues(self) -> None:
        raw = b"WEBVTT\nKind: captions\nLanguage: en\n\nNOTE1\n00:01.000 --> 00:02.000\nFirst cue.\n\nNOTE comment\nNot spoken.\n\nreal-id\n00:03.000 --> 00:04.000\nSecond cue."
        value = imports.extract_uploaded("scene.vtt", raw)
        self.assertEqual([cue.cue_id for chapter in value.chapters for cue in chapter.source_cues], ["NOTE1", "real-id"])
        self.assertNotIn("Not spoken", value.chapters[0].text)

    def test_subtitle_literal_angle_brackets_remain_text(self) -> None:
        value = imports.extract_uploaded("scene.srt", b"1\n00:00:00,000 --> 00:00:01,000\n2 < 3 and 4 > 1.")
        self.assertEqual(value.chapters[0].source_cues[0].text, "2 < 3 and 4 > 1.")

    def test_subtitle_identifier_is_preserved_exactly(self) -> None:
        value = imports.extract_uploaded("scene.vtt", b"WEBVTT\n\n  original identifier  \n00:00.000 --> 00:01.000\nHello.")
        self.assertEqual(value.chapters[0].source_cues[0].cue_id, "  original identifier  ")

    def test_subtitle_errors_never_silently_drop_cues(self) -> None:
        for raw in (b"1\n00:03,000 --> 00:01,000\nBackwards", b"1\ninvalid timing\nText", b"1\n00:00:00,000 --> 00:00:01,000\n", b"WEBVTT\n\n1\n00:00.000 --> 00:01.000\n<v Alice>A<v Bob>B"):
            with self.subTest(raw=raw), self.assertRaises(imports.EbookImportError):
                imports.extract_uploaded("scene.vtt" if raw.startswith(b"WEBVTT") else "scene.srt", raw)

    def test_reimport_creates_independent_draft_and_preserves_edits_and_original_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.object(audiobooks, "BOOKS_ROOT", Path(directory) / "books"):
            source = b"First version"
            first = imports.save_draft("story.txt", source, imports.extract_uploaded("story.txt", source))
            from app.audiobook_contracts import PatchEbookDraftRequest
            imports.patch_draft(first.id, PatchEbookDraftRequest(title="Reviewed edit", chapters=first.chapters, revision=1))
            second = imports.save_draft("story.txt", source, imports.extract_uploaded("story.txt", source))
            self.assertNotEqual(first.id, second.id)
            self.assertEqual(imports.get_draft(first.id).title, "Reviewed edit")
            self.assertEqual(imports.source_path(first.id).read_bytes(), source)
            self.assertIn("independent_reimport", [warning.code for warning in second.warnings])

    def test_editing_subtitle_draft_cannot_erase_original_cue_provenance(self) -> None:
        from app.audiobook_contracts import EbookChapterDraft, PatchEbookDraftRequest
        with tempfile.TemporaryDirectory() as directory, patch.object(audiobooks, "BOOKS_ROOT", Path(directory) / "books"):
            source = b"original\n00:00:00,000 --> 00:00:01,000\nAlice: Hello."
            draft = imports.save_draft("scene.srt", source, imports.extract_uploaded("scene.srt", source))
            saved = imports.patch_draft(draft.id, PatchEbookDraftRequest(title="Edited", revision=1,
                chapters=[EbookChapterDraft(title=draft.chapters[0].title, text="Alice: Edited dialogue.")]))
            self.assertEqual(saved.chapters[0].source_cues, draft.chapters[0].source_cues)
            self.assertTrue(saved.cast_review_required)

    def test_render_requires_explicit_review_and_every_preserved_speaker_mapping(self) -> None:
        from app.audiobook_contracts import CreateAudiobookFromDraftRequest
        with tempfile.TemporaryDirectory() as directory, patch.object(audiobooks, "BOOKS_ROOT", Path(directory) / "books"):
            source = b"original\n00:00:00,000 --> 00:00:01,000\nAlice: Hello."
            draft = imports.save_draft("scene.srt", source, imports.extract_uploaded("scene.srt", source))
            for body in (CreateAudiobookFromDraftRequest(profile_id="a" * 32, revision=1),
                         CreateAudiobookFromDraftRequest(profile_id="a" * 32, revision=1, cast_reviewed=True)):
                with self.assertRaises(imports.EbookImportError) as failure:
                    imports.create_from_draft(draft.id, body)
                self.assertEqual(failure.exception.code, "subtitle_cast_review_required")
