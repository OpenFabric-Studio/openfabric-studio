"""Chapter cache, cue sheets, language readiness, and pause splits."""
from __future__ import annotations

import os
import tempfile
import unittest
import wave
import zipfile
from pathlib import Path
from pydantic import ValidationError
from unittest.mock import patch

from app import audiobook_collection, audiobook_narration, audiobooks, narration_pauses, speech_clone, voice_profiles
from app.audiobook_contracts import AudiobookChapterInput, CreateAudiobookRequest, SetAudiobookLanguagesRequest


def _tone(path: Path, seconds: float, amplitude: int, rate: int = 16000) -> None:
    frames = int(seconds * rate)
    payload = b"".join(int(amplitude).to_bytes(2, "little", signed=True) for _ in range(frames))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(payload)


class PauseChunkTests(unittest.TestCase):
    def test_split_lands_in_silence_not_in_the_tone(self) -> None:
        rate = 16000
        tone = b"".join((8000).to_bytes(2, "little", signed=True) for _ in range(rate))
        gap = b"\x00\x00" * int(rate * 0.4)
        spans = narration_pauses.chunk_pcm(tone + gap + tone, rate, 2, 1)
        self.assertEqual(len(spans), 2)
        split = spans[0][1]
        self.assertGreater(split, rate)
        self.assertLess(split, rate + int(rate * 0.4))

    def test_continuous_tone_is_not_cut(self) -> None:
        rate = 16000
        tone = b"".join((4000).to_bytes(2, "little", signed=True) for _ in range(rate))
        self.assertEqual(narration_pauses.chunk_pcm(tone, rate, 2, 1), [(0, rate)])

    def test_text_split_prefers_a_pause_mark(self) -> None:
        sentence = "alpha beta, gamma delta. "
        text = sentence * 80
        sections = audiobook_narration.split_sections(text)
        self.assertGreater(len(sections), 1)
        for section in sections[:-1]:
            self.assertFalse(section.endswith("gam"))
            self.assertTrue(section.rstrip().endswith((",", ".", "!", "?", ";", ":")) or section.endswith("\n"))


class AudiobookResearchTests(unittest.TestCase):
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
        self.profile = voice_profiles.create_profile(
            name="Reader", consent_confirmed=True, audio_bytes=b"RIFF....WAVE", filename="ref.wav", notes="Reference.",
        )
        self.calls = 0
        original = speech_clone.synthesize_to_path

        def counted(*, profile_id: str, text: str, output_path: Path, prompt_text: str | None = None,
                    prompt_language: str | None = None, text_language: str | None = None,
                    require_consent: bool = True) -> object:
            self.calls += 1
            return original(profile_id=profile_id, text=text, output_path=output_path, prompt_text=prompt_text,
                            prompt_language=prompt_language, text_language=text_language, require_consent=require_consent)

        self.synth = patch.object(speech_clone, "synthesize_to_path", side_effect=counted)
        self.synth.start()

    def tearDown(self) -> None:
        self.synth.stop()
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()

    def _create(self, language: str = "en") -> str:
        return audiobooks.create_book(CreateAudiobookRequest(
            title="Field Notes", profile_id=self.profile.id, author="Ada", language=language,
            chapters=[AudiobookChapterInput(title="One", text="A short narration.")],
        )).book.id

    def test_cache_languages_cue_and_collection(self) -> None:
        first = self._create()
        self.assertEqual(audiobooks.get_book(first).status, "done")
        self.assertEqual(self.calls, 1)
        jobs = audiobooks.list_jobs(book_id=first)
        self.assertTrue(jobs[0].language_ready)
        self.assertEqual(jobs[0].language, "en")
        cue = audiobook_collection.cue_text(first)
        self.assertIn("TRACK 01 AUDIO", cue)
        self.assertIn("INDEX 01 00:00:00", cue)
        archive = audiobook_collection.write_collection(first)
        with zipfile.ZipFile(archive) as handle:
            manifest = handle.read("MANIFEST.txt").decode()
            self.assertIn("language: en", manifest)
            self.assertIn("\tready\t", manifest)
            self.assertIn("book.cue", handle.namelist())
            self.assertIn("export.wav", handle.namelist())
        second = self._create("es")
        self.assertEqual(self.calls, 1)
        self.assertEqual(audiobooks.list_jobs(book_id=second)[0].language, "es")
        updated = audiobooks.set_languages(second, "fr", [(0, "de")])
        self.assertEqual(updated.language, "fr")
        self.assertEqual(audiobooks.list_jobs(book_id=second)[0].language, "de")
        with self.assertRaises(ValidationError):
            SetAudiobookLanguagesRequest(language="not a language")
