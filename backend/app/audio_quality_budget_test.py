from __future__ import annotations

from pathlib import Path
import struct
import tempfile
import unittest

from app import audio_quality


class AudioQualityBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_optional_cap_preserves_default_and_refuses_capped_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.wav'
            source.write_bytes(b'original')
            commands: list[list[str]] = []
            overflow = False

            async def run(argv: list[str], timeout: float) -> bytes:
                commands.append(argv)
                if '-show_entries' in argv:
                    return b'{"streams":[{"sample_rate":"8000","channels":1}]}'
                if 'null' in argv:
                    return b'ffmpeg version test\n{"input_i":"-20","input_tp":"-10","input_lra":"0","input_thresh":"-30","target_offset":"0"}'
                count = int(argv[argv.index('-fs') + 1]) // 4 if overflow else 8000
                Path(argv[-1]).write_bytes(struct.pack('<f', .1) * count)
                return b''

            default = await audio_quality.measure(source, root, run)
            self.assertEqual(default.metrics.samples_analyzed, 8000)
            self.assertNotIn('asetnsamples=n=1024:p=0', commands[-1])
            self.assertEqual(commands[-1][commands[-1].index('-fs') + 1], str(audio_quality.MAX_DECODED_BYTES))
            bounded = await audio_quality.measure(source, root, run, max_decoded_bytes=39296)
            self.assertEqual(bounded.metrics.samples_analyzed, 8000)
            self.assertIn('asetnsamples=n=1024:p=0', commands[-1])
            self.assertEqual(commands[-1][commands[-1].index('-fs') + 1], '35200')
            overflow = True
            with self.assertRaises(audio_quality.AudioQualityError) as error:
                await audio_quality.measure(source, root, run, max_decoded_bytes=39296)
            self.assertEqual(error.exception.code, 'audio_decode_budget_exceeded')
            self.assertEqual(source.read_bytes(), b'original')
            self.assertFalse((root / 'source.analysis.f32').exists())
