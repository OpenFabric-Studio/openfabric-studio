"""Line-by-line cast: narrator by default, a saved voice when a line names one."""
from __future__ import annotations

import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app import audiobook_cast, audiobook_collection, audiobooks, speech_clone, voice_profiles
from app.audiobook_contracts import AudiobookChapterInput, CastMember, CreateAudiobookRequest


class SplitTurnTests(unittest.TestCase):
    def test_unlabeled_lines_stay_with_the_narrator_and_labels_are_not_spoken(self) -> None:
        narrator = "b" * 32
        alice = "a" * 32
        cast = [CastMember(name="Alice", profile_id=alice)]
        turns = audiobook_cast.split_turns(
            "The road was quiet.\nAlice: I know a shorter way.\nBob: not a speaker\nAlice: This way.",
            narrator,
            cast,
        )
        self.assertEqual(turns[0], (narrator, "Narrator", "The road was quiet."))
        self.assertEqual(turns[1], (alice, "Alice", "I know a shorter way."))
        self.assertEqual(turns[2], (narrator, "Narrator", "Bob: not a speaker"))
        self.assertEqual(turns[3], (alice, "Alice", "This way."))
        self.assertNotIn("Alice:", turns[1][2])


class CastNarrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.patches = [
            patch.object(audiobooks, "BOOKS_ROOT", self.root / "books"),
            patch.object(voice_profiles, "PROFILES_ROOT", self.root / "profiles"),
            patch.dict(os.environ, {"OPENFABRIC_AUDIOBOOK_SYNC": "1", "OPENFABRIC_SPEECH_CLONE_MOCK": "1"}),
        ]
        for item in self.patches:
            item.start()
        self.narrator = voice_profiles.create_profile(
            name="Reader", consent_confirmed=True, audio_bytes=b"RIFF....WAVE", filename="ref.wav", notes="Reference.",
        )
        self.alice = voice_profiles.create_profile(
            name="Alice", consent_confirmed=True, audio_bytes=b"RIFF....WAVE", filename="alice.wav", notes="Reference.",
        )
        self.calls: list[tuple[str, str]] = []
        original = speech_clone.synthesize_to_path

        def counted(*, profile_id: str, text: str, output_path: Path, prompt_text: str | None = None,
                    prompt_language: str | None = None, text_language: str | None = None,
                    require_consent: bool = True) -> object:
            self.calls.append((profile_id, text))
            return original(
                profile_id=profile_id, text=text, output_path=output_path, prompt_text=prompt_text,
                prompt_language=prompt_language, text_language=text_language, require_consent=require_consent,
            )

        self.synth = patch.object(speech_clone, "synthesize_to_path", side_effect=counted)
        self.synth.start()

    def tearDown(self) -> None:
        self.synth.stop()
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()

    def test_lines_use_cast_voices_and_one_chapter_file_records_speakers(self) -> None:
        created = audiobooks.create_book(CreateAudiobookRequest(
            title="Cast Book",
            profile_id=self.narrator.id,
            cast=[CastMember(name="Alice", profile_id=self.alice.id)],
            chapters=[AudiobookChapterInput(
                title="One",
                text="The road was quiet.\nAlice: I know a shorter way.",
            )],
        ))
        book_id = created.book.id
        self.assertEqual(created.book.cast[0].name, "Alice")
        self.assertEqual(audiobooks.get_book(book_id).status, "done")
        self.assertEqual(
            self.calls,
            [
                (self.narrator.id, "The road was quiet."),
                (self.alice.id, "I know a shorter way."),
            ],
        )
        self.assertTrue(audiobooks.chapter_audio_path(book_id, 0).is_file())
        self.assertTrue(audiobooks.export_path_for(book_id).is_file())
        cue = audiobook_collection.cue_text(book_id)
        self.assertIn('REM SPEAKER "Narrator"', cue)
        self.assertIn('REM SPEAKER "Alice"', cue)
        self.assertIn("TRACK 01 AUDIO", cue)
        archive = audiobook_collection.write_collection(book_id)
        with zipfile.ZipFile(archive) as handle:
            names = handle.namelist()
            self.assertIn("MANIFEST.txt", names)
            self.assertIn("book.cue", names)
            self.assertIn("export.wav", names)
            self.assertTrue(any(name.startswith("chapters/") for name in names))
            manifest = handle.read("MANIFEST.txt").decode()
        self.assertIn("speakers Narrator 0;Alice ", manifest)
        updated = audiobooks.set_chapter_text(book_id, 0, "Alice: Only Alice speaks now.")
        self.assertEqual(updated.status, "done")
        self.assertIn("Only Alice speaks now.", audiobooks.list_jobs(book_id=book_id)[0].chapter_text)

    def test_duplicate_cast_name_is_rejected(self) -> None:
        with self.assertRaises(audiobooks.AudiobookError) as caught:
            audiobooks.create_book(CreateAudiobookRequest(
                title="Cast Book",
                profile_id=self.narrator.id,
                cast=[
                    CastMember(name="Alice", profile_id=self.alice.id),
                    CastMember(name="alice", profile_id=self.narrator.id),
                ],
                chapters=[AudiobookChapterInput(title="One", text="Hello.")],
            ))
        self.assertEqual(caught.exception.code, "duplicate_cast_name")
