"""Pinned setup must preserve existing checkouts/environments and avoid implicit weights."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class SetupTests(unittest.TestCase):
    def test_verify_only_never_installs_or_downloads(self) -> None:
        import scripts.setup_video as setup
        with tempfile.TemporaryDirectory() as scratch:
            cache = Path(scratch)
            with patch.object(sys, 'argv', ['setup_video.py', '--engine-dir', str(cache / 'engine'),
                    '--cache-dir', str(cache), '--verify-only']), \
                    patch.object(setup, 'ensure_compatibility'), \
                    patch.object(setup, 'verify_and_record_artifacts', return_value='a' * 64) as verify, \
                    patch.object(setup, 'prepare_engine') as prepare, \
                    patch.object(setup, 'sync_environment') as sync, patch('builtins.print'):
                self.assertEqual(setup.main(), 0)
            verify.assert_called_once_with(cache, 'ltx23')
            prepare.assert_not_called()
            sync.assert_not_called()

    def test_setup_helper_exists(self) -> None:
        self.assertIsNotNone(importlib.util.find_spec('scripts.setup_video'), 'Non-destructive pinned setup helper is required')

    def test_existing_non_engine_directory_is_preserved(self) -> None:
        from scripts.setup_video import prepare_engine
        from app.video_engine import VideoEngineError
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch) / 'engine'
            root.mkdir()
            sentinel = root / 'user-change.txt'
            sentinel.write_text('keep me')
            with patch('subprocess.run') as boundary, self.assertRaises(VideoEngineError) as failed:
                prepare_engine(root)
            self.assertEqual(failed.exception.code, 'engine_checkout_conflict')
            self.assertEqual(sentinel.read_text(), 'keep me')
            boundary.assert_not_called()

    def test_dirty_checkout_is_not_reset_or_synced(self) -> None:
        from scripts.setup_video import prepare_engine
        from app.video_engine import ENGINE_COMMIT, VideoEngineError
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            (root / '.git').mkdir()
            (root / 'pyproject.toml').write_text('user modified')
            with patch('subprocess.run', side_effect=[
                subprocess.CompletedProcess([], 0, ENGINE_COMMIT + '\n', ''),
                subprocess.CompletedProcess([], 0, ' M pyproject.toml\n', '')]) as boundary:
                with self.assertRaises(VideoEngineError) as failed:
                    prepare_engine(root)
            self.assertEqual(failed.exception.code, 'engine_checkout_dirty')
            self.assertEqual(boundary.call_count, 2)
            self.assertEqual((root / 'pyproject.toml').read_text(), 'user modified')

    def test_existing_environment_uses_offline_read_only_check_and_preserves_extra_packages(self) -> None:
        from scripts.setup_video import sync_environment
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            (root / '.venv').mkdir()
            with patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0)) as boundary:
                sync_environment(root)
            self.assertEqual(boundary.call_args.args[0],
                ['uv', 'sync', '--frozen', '--no-dev', '--inexact', '--offline', '--check'])

    def test_download_arguments_use_only_required_files_and_exact_revisions(self) -> None:
        from scripts.setup_video import download_argv
        from app.video_engine import model_packs
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            commands = download_argv(root / 'engine', root / 'cache', 'ltx23')
            self.assertEqual(len(commands), 2)
            pack = model_packs()['ltx23']
            self.assertEqual(commands[0][2], pack.repo_id)
            self.assertIn(pack.revision, commands[0])
            self.assertIn('--cache-dir', commands[0])
            self.assertIn('transformer-distilled-1.1.safetensors', commands[0])
            self.assertNotIn('transformer-distilled.safetensors', commands[0])
            self.assertNotIn('--force-download', commands[0])
            self.assertFalse((root / 'cache').exists())
