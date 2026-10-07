"""Research-only candidate must never weaken production source guards."""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class VideoCandidateTests(unittest.TestCase):
    def test_candidate_parser_accepts_reviewed_modality_tiling_only_once(self) -> None:
        from app.video_candidate import candidate_tiling_cli
        from app.video_engine import VideoEngineError
        source = '_add_generation_args(a2v, modality_tiling=False)\n'
        self.assertEqual(candidate_tiling_cli(source), '_add_generation_args(a2v, modality_tiling=True)\n')
        with self.assertRaises(VideoEngineError):
            candidate_tiling_cli(source + source)

    def test_candidate_a2v_uses_explicit_audio_and_refuses_unreviewed_adapters(self) -> None:
        from app.video_candidate import CandidateSettings, candidate_argv
        from app.video_engine import VideoEngineError
        with tempfile.TemporaryDirectory() as temporary, patch('app.video_candidate.verify_candidate'):
            root = Path(temporary)
            audio = root / 'audio.wav'
            audio.write_bytes(b'fixture')
            argv = candidate_argv(root, root / 'cache', root / 'out', CandidateSettings(source_audio=audio))
            self.assertIn('a2v', argv)
            self.assertNotIn('--no-audio', argv)
            self.assertEqual(argv[argv.index('--audio') + 1], str(audio.resolve()))
            with self.assertRaisesRegex(VideoEngineError, 'candidate_a2v_adapter_unreviewed'):
                candidate_argv(root, root / 'cache', root / 'out', CandidateSettings(source_audio=audio, adapter=root / 'adapter.safetensors'))

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
