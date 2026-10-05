from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from pydantic import ValidationError
from app import audiobooks, narration_pauses as pauses
from app.audiobook_review_contracts import PauseAnalysisSettings


class PauseSettingsTests(unittest.TestCase):
    def test_defaults_cover_every_pcm_frame_without_overlap_or_gap(self) -> None:
        tone = (10000).to_bytes(2, "little", signed=True) * 8000
        pcm = tone + b"\0\0" * 4000 + tone
        spans = pauses.chunk_pcm(pcm, 8000, 2, 1)
        self.assertEqual(spans, [(0, 9920), (9920, 20000)])
        self.assertEqual(b"".join(pcm[start * 2:end * 2] for start, end in spans), pcm)

    def test_sensitivity_minimum_pause_and_context_padding_are_bounded_and_effective(self) -> None:
        tone = (10000).to_bytes(2, "little", signed=True) * 8000
        quiet = (2000).to_bytes(2, "little", signed=True) * 4000
        pcm = tone + quiet + tone
        self.assertEqual(pauses.chunk_pcm(pcm, 8000, 2, 1), [(0, 20000)])
        spans = pauses.chunk_pcm(pcm, 8000, 2, 1, settings=PauseAnalysisSettings(energy_ratio=0.25, padding_ms=100))
        self.assertGreater(len(spans), 1)
        self.assertGreater(spans[0][1], spans[1][0])
        self.assertEqual(spans[0][0], 0)
        self.assertEqual(spans[-1][1], 20000)
        self.assertEqual(pauses.chunk_pcm(pcm, 8000, 2, 1, settings=PauseAnalysisSettings(energy_ratio=0.25, min_silence_ms=1000)), [(0, 20000)])
        for data in ({"energy_ratio": 0}, {"min_silence_ms": 0}, {"padding_ms": -1}, {"energy_ratio": float("nan")}):
            with self.assertRaises(ValidationError):
                PauseAnalysisSettings.model_validate(data)

    def test_settings_round_trip_and_symlink_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.object(audiobooks, "BOOKS_ROOT", Path(directory) / "books"):
            value = PauseAnalysisSettings(energy_ratio=0.2, min_silence_ms=500, padding_ms=30)
            pauses.save_settings(value)
            self.assertEqual(pauses.load_settings(), value)
            location = audiobooks.books_root() / ".pause-analysis.json"
            location.unlink()
            private = Path(directory) / "private.json"
            private.write_text(value.model_dump_json())
            location.symlink_to(private)
            with self.assertRaises(ValueError):
                pauses.save_settings(PauseAnalysisSettings())
