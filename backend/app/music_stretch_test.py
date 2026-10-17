from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

import numpy as np
import soundfile as sf

from app import music_stretch
from app.music_dsp import MusicDspError
from scripts.music_analysis_worker import run_command


class RubberBandStretchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.executable = self.root / 'rubberband'
        self.executable.write_bytes(b'private fake executable; never launched')
        self.digest = hashlib.sha256(self.executable.read_bytes()).hexdigest()
        self.renderer = music_stretch.RubberBandStretch(self.executable, self.digest)
        self.source = self.root / 'source.wav'
        self.output = self.root / 'output.wav'
        self.samples = np.column_stack((np.full(4800, 4.0, dtype=np.float32), np.full(4800, -4.0, dtype=np.float32)))
        sf.write(self.source, self.samples, 48000, subtype='FLOAT')

    async def render(self, run: music_stretch.RunCommand, *, anchors: tuple[tuple[int, int], ...] = ((0, 0), (4800, 6000))):
        return await self.renderer(self.source, self.output, sample_rate_hz=48000, channels=2,
            input_frames=4800, output_frames=6000, anchors=anchors, pitch_shift_semitones=0.0, run=run)

    async def test_unverified_executable_and_existing_destination_never_launch(self) -> None:
        run = AsyncMock()
        self.executable.write_bytes(b'changed')
        with self.assertRaises(MusicDspError) as error:
            await self.render(run)
        self.assertEqual(error.exception.code, 'dsp_runtime_unverified')
        run.assert_not_awaited()
        self.executable.write_bytes(b'private fake executable; never launched')
        self.output.write_bytes(b'keep me')
        with self.assertRaises(MusicDspError):
            await self.render(run)
        self.assertEqual(self.output.read_bytes(), b'keep me')
        run.assert_not_awaited()

    async def test_headroom_is_compensated_and_linked_without_clipping(self) -> None:
        commands: list[list[str]] = []
        async def run(argv: list[str], timeout: float) -> bytes:
            commands.append(argv)
            if argv[0] != str(self.executable):
                return await run_command(argv, timeout)
            if argv[1:] == ['--version']:
                return b'4.0.0\n'
            samples, rate = sf.read(argv[-2], dtype='float32', always_2d=True)
            self.assertEqual(float(np.max(np.abs(samples))), .25)
            sf.write(argv[-1], np.tile(samples[:1], (6000, 1)), rate, subtype='FLOAT')
            return b''
        identity = await self.render(run)
        result, _ = sf.read(self.output, dtype='float32', always_2d=True)
        np.testing.assert_array_equal(result, np.tile(self.samples[:1], (6000, 1)))
        self.assertEqual(identity.input_gain_power_of_two, -4)
        self.assertEqual(identity.output_gain_power_of_two, 4)
        self.assertEqual(identity.executable_sha256, self.digest)
        self.assertIn('r3', identity.recipe)
        native = next(argv for argv in commands if '--time' in argv)
        self.assertIn('--ignore-clipping', native)
        self.assertIn('--centre-focus', native)
        self.assertIn('-3', native)

    async def test_nonuniform_uses_explicit_r2_map_and_short_output_is_rejected(self) -> None:
        async def run(argv: list[str], timeout: float) -> bytes:
            if argv[0] != str(self.executable):
                return await run_command(argv, timeout)
            if argv[1:] == ['--version']:
                return b'4.0.0\n'
            self.assertIn('-2', argv)
            map_path = Path(argv[argv.index('--timemap') + 1])
            self.assertEqual(map_path.read_text(), '0 0\n2400 3300\n4800 6000\n')
            sf.write(argv[-1], np.zeros((5999, 2), dtype=np.float32), 48000, subtype='FLOAT')
            return b''
        with self.assertRaises(MusicDspError) as error:
            await self.render(run, anchors=((0, 0), (2400, 3300), (4800, 6000)))
        self.assertEqual(error.exception.code, 'dsp_output_invalid')
        self.assertFalse(self.output.exists())
        np.testing.assert_array_equal(sf.read(self.source, dtype='float32', always_2d=True)[0], self.samples)

    async def test_native_full_scale_and_nonfinite_outputs_are_rejected(self) -> None:
        for value in (1.0, float('nan')):
            async def run(argv: list[str], timeout: float) -> bytes:
                if argv[0] != str(self.executable):
                    return await run_command(argv, timeout)
                if argv[1:] == ['--version']:
                    return b'4.0.0\n'
                sf.write(argv[-1], np.full((6000, 2), value, dtype=np.float32), 48000, subtype='FLOAT')
                return b''
            with self.subTest(value=value), self.assertRaises(MusicDspError) as error:
                await self.render(run)
            self.assertEqual(error.exception.code, 'dsp_output_invalid')
            self.assertFalse(self.output.exists())

    async def test_collapsed_or_out_of_range_frame_maps_fail_before_command(self) -> None:
        for anchors in (((0, 0), (0, 1), (4800, 6000)), ((0, 0), (100, 200), (4800, 6000)),
                        ((1, 0), (4800, 6000)), ((0, 0), (4799, 6000))):
            run = AsyncMock()
            with self.subTest(anchors=anchors), self.assertRaises(MusicDspError):
                await self.render(run, anchors=anchors)
            run.assert_not_awaited()
