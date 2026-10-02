"""The native reader requires WAV even when the retained source is FLAC."""
from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import soundfile as sf
import numpy as np
from app import db, native_yue, reference_melody
from app.contracts import JsonObject
from app.reference_contracts import ReferenceToolCapability


class MelodyTests(unittest.IsolatedAsyncioTestCase):
    async def test_lossless_source_is_normalized_to_wav_without_overwriting_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / 'files' / 'source.flac'
            source.parent.mkdir()
            sf.write(str(source), np.full(4800, 0.1, dtype=np.float32), 48000, format='FLAC')
            original = source.read_bytes()
            workspace = root / 'work'; workspace.mkdir()
            class Session:
                async def post(self, endpoint: str, payload: JsonObject) -> JsonObject:
                    if endpoint.endswith('run'):
                        request = payload['request']
                        assert isinstance(request, dict)
                        audio_path = request['audio']
                        assert isinstance(audio_path, str)
                        path = Path(audio_path)
                        info = sf.info(str(path))
                        self_test.assertIn(info.format, ('WAV', 'WAVEX'))
                        self_test.assertEqual(info.subtype, 'FLOAT')
                        self_test.assertEqual(info.samplerate, 48000)
                        self_test.assertEqual(info.channels, 1)
                        self_test.assertEqual(info.frames, 4800)
                        self_test.assertNotEqual(path, source)
                        return {'artifacts': [{'id': 'score', 'meta': {'format': 'abc'}, 'payload': base64.b64encode(b'X:1\nK:C\nC D|').decode()}]}
                    return {}
            self_test = self
            @asynccontextmanager
            async def session() -> AsyncIterator[Session]:
                yield Session()

            async def write_wavex(command: list[str], cwd: Path, timeout: float) -> bytes:
                audio, rate = sf.read(str(source), dtype='float32')
                sf.write(command[-1], audio, rate, format='WAVEX', subtype='FLOAT')
                return b''

            with patch.object(db, 'FILES_DIR', root / 'files'), patch.object(reference_melody, 'capability', return_value=ReferenceToolCapability(available=True)), patch.object(native_yue, 'owned_session', side_effect=session):
                for extensible in (False, True):
                    with self.subTest(extensible=extensible):
                        result = await reference_melody.prepare_reference_melody(source, workspace=workspace, on_proc=lambda proc: None,
                            run_tool=write_wavex if extensible else None)
                        self.assertIn('K:C', result)
                        self.assertEqual(source.read_bytes(), original)
                        self.assertFalse((workspace / 'melody-input.wav').exists())
