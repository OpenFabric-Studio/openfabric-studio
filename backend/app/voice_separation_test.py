from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import stems


class VoiceSeparationTests(unittest.TestCase):
    def test_high_mode_selects_fine_tuned_demucs(self) -> None:
        command = stems._demucs_cmd(Path('song.wav'), Path('out'), quality='high')
        self.assertEqual(command[command.index('-n') + 1], 'htdemucs_ft')

    def test_invalid_quality_does_not_silently_fall_back(self) -> None:
        with self.assertRaises(ValueError):
            stems._demucs_cmd(Path('song.wav'), Path('out'), quality='typo')

    def test_optional_roformer_is_disabled_until_all_artifacts_exist(self) -> None:
        from app import voice_separation as separation
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(separation, 'ROFORMER_DIR', root), patch.object(separation, 'ROFORMER_CONFIG', root/'config.yaml'), patch.object(separation, 'ROFORMER_CHECKPOINT', root/'weights.ckpt'):
                options = separation.separation_options()
                self.assertFalse(next(option.available for option in options.options if option.id == 'roformer'))

    def test_roformer_result_cannot_escape_output_root(self) -> None:
        from app.voice_separation import find_vocal_output
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root/'outside.wav'
            outside.write_bytes(b'not audio')
            output = root/'out'
            output.mkdir()
            (output/'source_vocals.wav').symlink_to(outside)
            with self.assertRaises(ValueError):
                find_vocal_output(output)


class OptionalSeparationProcessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from app import voice_separation as separation
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.engine = self.root / 'engine'
        (self.engine / 'utils').mkdir(parents=True)
        (self.engine / 'inference.py').write_text('# mocked engine')
        (self.engine / 'utils/settings.py').write_text('--model_type --config_path --start_check_point --input_folder --store_dir --pcm_type --filename_template')
        self.config, self.checkpoint = self.root / 'config.yaml', self.root / 'weights.ckpt'
        self.config.write_text('config')
        self.checkpoint.write_bytes(b'weights')
        self.audio = self.root / 'song.wav'
        self.audio.write_bytes(b'original source')
        self.output = self.root / 'result'
        self.contexts = [patch.object(separation, 'ROFORMER_DIR', self.engine), patch.object(separation, 'ROFORMER_CONFIG', self.config),
            patch.object(separation, 'ROFORMER_CHECKPOINT', self.checkpoint), patch.object(separation, 'LOG_DIR', self.root / 'logs'),
            patch.object(separation, 'roformer_python', return_value=Path(sys.executable))]
        for context in self.contexts:
            context.start()

    async def asyncTearDown(self) -> None:
        for context in reversed(self.contexts):
            context.stop()
        self.temporary.cleanup()

    async def test_adapter_uses_verified_float_output_flags_and_single_copied_input(self) -> None:
        from app import voice_separation as separation
        process = AsyncMock()
        process.wait.return_value = 0
        def create_output(*command: str, **options: object) -> AsyncMock:
            output = Path(command[command.index('--store_dir') + 1])
            (output / 'source_vocals.wav').write_bytes(b'vocal output')
            return process
        with patch.object(separation, 'spawn_process', new=AsyncMock(side_effect=create_output)) as spawn:
            result = await separation.separate_vocal(self.audio, self.output, quality='roformer', log_name='trial')
        command = spawn.call_args.args
        self.assertEqual(command[command.index('--pcm_type') + 1], 'FLOAT')
        self.assertEqual(command[command.index('--filename_template') + 1], '{file_name}_{instr}')
        self.assertEqual(list((self.output / 'input').iterdir()), [self.output / 'input/source.wav'])
        self.assertEqual(result.read_bytes(), b'vocal output')
        self.assertEqual(self.audio.read_bytes(), b'original source')

    async def test_cancellation_terminates_owned_child_and_releases_gpu_lock(self) -> None:
        from app import voice_separation as separation
        from app.job_lifecycle import spawn_process
        started = asyncio.Event()
        children: list[asyncio.subprocess.Process] = []
        tracked: list[asyncio.subprocess.Process | None] = []
        async def launch(*command: str, **options: object) -> asyncio.subprocess.Process:
            child = await spawn_process(sys.executable, '-c', 'import time; time.sleep(60)')
            children.append(child)
            started.set()
            return child
        with patch.object(separation, 'spawn_process', new=AsyncMock(side_effect=launch)):
            task = asyncio.create_task(separation.separate_vocal(self.audio, self.output, quality='roformer', log_name='cancel', on_proc=tracked.append))
            await asyncio.wait_for(started.wait(), timeout=3)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertIsNotNone(children[0].returncode)
        self.assertEqual(tracked, [children[0], None])
        self.assertFalse(self.output.exists())
        self.assertFalse(separation.gpu_lock.locked())

    async def test_unknown_cli_signature_fails_before_spawn(self) -> None:
        from app import voice_separation as separation
        (self.engine / 'utils/settings.py').write_text('--some_new_api')
        with patch.object(separation, 'spawn_process', new=AsyncMock()) as spawn:
            with self.assertRaises(separation.VoiceSeparationError) as failure:
                await separation.separate_vocal(self.audio, self.output, quality='roformer', log_name='invalid')
        self.assertEqual(failure.exception.code, 'roformer_incompatible')
        spawn.assert_not_called()

    async def test_engine_failure_removes_partial_output_and_preserves_source(self) -> None:
        from app import voice_separation as separation
        process = AsyncMock()
        process.wait.return_value = 1
        def create_partial(*command, **options):
            destination = Path(command[command.index('--store_dir') + 1])
            (destination / 'source_vocals.wav').write_bytes(b'partial output')
            return process
        with patch.object(separation, 'spawn_process', new=AsyncMock(side_effect=create_partial)), patch.object(separation, 'kill_process_tree', new=AsyncMock()):
            with self.assertRaises(separation.VoiceSeparationError) as raised:
                await separation.separate_vocal(self.audio, self.output, quality='roformer', log_name='failure')
        self.assertEqual(raised.exception.code, 'separation_failed')
        self.assertFalse(self.output.exists())
        self.assertEqual(self.audio.read_bytes(), b'original source')
        self.assertFalse(separation.gpu_lock.locked())

    async def test_missing_vocal_is_a_stable_error_and_removes_output(self) -> None:
        from app import voice_separation as separation
        process = AsyncMock()
        process.wait.return_value = 0
        with patch.object(separation, 'spawn_process', new=AsyncMock(return_value=process)), patch.object(separation, 'kill_process_tree', new=AsyncMock()):
            with self.assertRaises(separation.VoiceSeparationError) as raised:
                await separation.separate_vocal(self.audio, self.output, quality='roformer', log_name='missing')
        self.assertEqual(raised.exception.code, 'no_vocal')
        self.assertFalse(self.output.exists())

    async def test_repeated_cancellation_waits_for_child_cleanup(self) -> None:
        from app import voice_separation as separation
        running, cleaning, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
        process = AsyncMock()
        tracked = []
        async def wait():
            running.set()
            await asyncio.Event().wait()
        async def drain(proc):
            cleaning.set()
            await finish.wait()
        process.wait.side_effect = wait
        with patch.object(separation, 'spawn_process', new=AsyncMock(return_value=process)), patch.object(separation, 'kill_process_tree', new=AsyncMock(side_effect=drain)):
            task = asyncio.create_task(separation.separate_vocal(self.audio, self.output, quality='roformer', log_name='repeat', on_proc=tracked.append))
            await running.wait()
            task.cancel()
            await cleaning.wait()
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            self.assertIs(tracked[-1], process)
            finish.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertIsNone(tracked[-1])
        self.assertFalse(self.output.exists())
        self.assertFalse(separation.gpu_lock.locked())
