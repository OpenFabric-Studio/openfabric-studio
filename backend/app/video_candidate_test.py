"""Research-only candidate must never weaken production source guards."""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class VideoCandidateTests(unittest.TestCase):
    def test_source_changes_and_symlink_escape_are_rejected(self) -> None:
        from app.video_candidate import candidate_digest, verify_candidate
        from app.video_engine import VideoEngineError
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / 'packages/ltx-core-mlx/src'
            package.mkdir(parents=True)
            source = package / 'fixture.py'
            source.write_text('x = 1\n')
            expected = candidate_digest(root)
            with patch('app.video_candidate.CANDIDATE_SHA256', expected):
                verify_candidate(root)
                source.write_text('x = 2\n')
                with self.assertRaises(VideoEngineError):
                    verify_candidate(root)
            source.unlink()
            external = root / 'external.py'
            external.write_text('x = 1\n')
            source.symlink_to(external)
            with self.assertRaises(VideoEngineError):
                candidate_digest(root)

    def test_low_ram_unfused_is_rejected_before_model_work(self) -> None:
        from app.video_candidate import CandidateSettings, candidate_argv
        from app.video_engine import VideoEngineError
        with self.assertRaisesRegex(VideoEngineError, 'candidate_unfused_requires_resident'):
            candidate_argv(Path('/engine'), Path('/cache'), Path('/output'),
                           CandidateSettings(memory_mode='low_ram', lora_mode='unfused'))

    def test_fixed_case_has_local_models_explicit_mode_and_no_a2v_claim(self) -> None:
        from app.video_candidate import CandidateSettings, candidate_argv
        with patch('app.video_candidate.verify_candidate'):
            argv = candidate_argv(Path('/engine'), Path('/cache'), Path('/output'),
                                  CandidateSettings(memory_mode='resident', lora_mode='unfused'))
        self.assertNotIn('--low-ram', argv)
        self.assertIn('--candidate', argv)
        self.assertIn('--two-stage', argv)
        self.assertIn('--no-audio', argv)
        self.assertEqual(argv[argv.index('--frames') + 1], '49')
        self.assertTrue(argv[argv.index('--model') + 1].startswith('/cache/'))

    def test_bad_adapter_and_geometry_are_rejected(self) -> None:
        from app.video_candidate import CandidateSettings, candidate_argv
        from app.video_engine import VideoEngineError
        with patch('app.video_candidate.verify_candidate'):
            with self.assertRaises(VideoEngineError):
                candidate_argv(Path('/engine'), Path('/cache'), Path('/output'),
                               CandidateSettings(adapter=Path('/missing.safetensors')))
            with self.assertRaises(VideoEngineError):
                candidate_argv(Path('/engine'), Path('/cache'), Path('/output'),
                               CandidateSettings(width=705))

    def test_optional_telemetry_replaces_link_and_cleans_failed_staging(self) -> None:
        from scripts.run_video import write_telemetry
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            victim = root / 'engine-file'
            victim.write_text('preserve')
            target = root / 'telemetry.json'
            target.symlink_to(victim)
            write_telemetry(target, 123)
            self.assertFalse(target.is_symlink())
            self.assertEqual(victim.read_text(), 'preserve')
            with patch('scripts.run_video.os.fsync', side_effect=OSError('fixture full disk')):
                with self.assertRaises(OSError):
                    write_telemetry(target, 456)
            self.assertEqual({path.name for path in root.iterdir()}, {'engine-file', 'telemetry.json'})
