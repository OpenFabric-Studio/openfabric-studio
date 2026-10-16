"""Regress ACE's training decoder without importing GPU/model dependencies."""
from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock


PATCH = Path(__file__).resolve().parents[2] / 'external/patches/ace-step.patch'
FIXTURES = Path(__file__).parent / 'fixtures/ace-audio'
PREPROCESS = 'acestep/training/dataset_builder_modules/preprocess_audio.py'
DATA_MODULE = 'acestep/training/data_module.py'


class AceTrainingAudioTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with tempfile.TemporaryDirectory(prefix='ace-audio-source-') as directory:
            root = Path(directory)
            for relative in (PREPROCESS, DATA_MODULE):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((FIXTURES / (target.name + '.txt')).read_bytes())
            sections = ['diff --git ' + section for section in PATCH.read_text().split('diff --git ')[1:]
                        if any(section.startswith(f'a/{path} b/{path}\n') for path in (PREPROCESS, DATA_MODULE))]
            if sections:
                subprocess.run(['git', 'apply', '--whitespace=nowarn', '-'], input=''.join(sections),
                               text=True, cwd=root, check=True, capture_output=True)
            cls.preprocess = ast.parse((root / PREPROCESS).read_text())
            cls.data_module = ast.parse((root / DATA_MODULE).read_text())

    def setUp(self) -> None:
        self.audio = Mock(shape=(2, 480))
        self.reader = Mock(return_value=(object(), 48000))
        self.converter = Mock(return_value=self.audio)
        self.torchaudio = Mock()
        self.torchaudio.load.side_effect = RuntimeError('Could not load libtorchcodec')
        self.torch = Mock()
        self.loader = self._function(self.preprocess, 'load_audio_stereo', {
            '_read_audio_file': self.reader,
            'IoAudioMixin': Mock(_numpy_to_channels_first=self.converter),
            'torch': self.torch, 'torchaudio': self.torchaudio,
        })

    def _function(self, module: ast.Module, name: str, namespace: dict[str, object]) -> object:
        body = [node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == name]
        self.assertEqual(len(body), 1, name)
        exec(compile(ast.Module(body=body, type_ignores=[]), '<patched-ace-audio>', 'exec'), namespace)
        function = namespace[name]
        if not callable(function):
            raise AssertionError('audio fixture does not contain a callable')
        return function

    def _load(self, rate: int = 48000, duration: float = 1.0) -> object:
        if not callable(self.loader):
            raise AssertionError('missing audio loader')
        return self.loader('temporary-sample.mp3', rate, duration)

    def test_preprocessing_uses_existing_reader_when_torchcodec_cannot_load(self) -> None:
        self.assertEqual(self._load(), (self.audio, 48000))
        self.reader.assert_called_once_with('temporary-sample.mp3')
        self.converter.assert_called_once_with(self.reader.return_value[0])
        self.torchaudio.load.assert_not_called()

    def test_mono_is_duplicated_and_truncated_after_resampling(self) -> None:
        self.reader.return_value = (object(), 24000)
        resampled = Mock(shape=(1, 96000))
        stereo = Mock(shape=(2, 96000))
        truncated = object()
        stereo.__getitem__ = Mock(return_value=truncated)
        resampled.repeat.return_value = stereo
        self.torchaudio.transforms.Resample.return_value.return_value = resampled

        self.assertEqual(self._load(duration=1.0), (truncated, 24000))
        self.torchaudio.transforms.Resample.assert_called_once_with(24000, 48000)
        self.torchaudio.transforms.Resample.return_value.assert_called_once_with(self.audio)
        resampled.repeat.assert_called_once_with(2, 1)
        stereo.__getitem__.assert_called_once_with((slice(None), slice(None, 48000)))

    def test_extra_channels_are_reduced_to_two(self) -> None:
        self.audio.shape = (4, 480)
        stereo = Mock(shape=(2, 480))
        self.audio.__getitem__ = Mock(return_value=stereo)
        self.assertEqual(self._load(), (stereo, 48000))
        self.audio.__getitem__.assert_called_once_with((slice(None, 2), slice(None)))

    def test_decoder_failure_propagates_without_producing_audio(self) -> None:
        self.reader.side_effect = RuntimeError('invalid audio fixture')
        with self.assertRaisesRegex(RuntimeError, 'invalid audio fixture'):
            self._load()
        self.converter.assert_not_called()

    def _raw_item(self, samples: int) -> tuple[object, Mock]:
        audio = Mock(shape=(2, samples))
        shared_loader = Mock(return_value=(audio, 48000))
        dataset = next(node for node in self.data_module.body
                       if isinstance(node, ast.ClassDef) and node.name == 'AceStepTrainingDataset')
        item = self._function(ast.Module(body=dataset.body, type_ignores=[]), '__getitem__', {
            'torch': self.torch, 'torchaudio': self.torchaudio, 'load_audio_stereo': shared_loader,
        })
        host = Mock(valid_samples=[{'audio_path': 'temporary-sample.mp3', 'caption': 'caption'}],
                    target_sample_rate=48000, max_duration=240.0)
        if not callable(item):
            raise AssertionError('missing raw audio dataset item')
        result = item(host, 0)
        shared_loader.assert_called_once_with('temporary-sample.mp3', 48000, 240.0)
        self.torchaudio.load.assert_not_called()
        return result, audio

    def test_raw_dataset_uses_shared_preprocessing_and_retains_five_second_padding(self) -> None:
        padded = Mock(shape=(2, 240000))
        self.torch.nn.functional.pad.return_value = padded
        result, audio = self._raw_item(48000)
        self.assertIsInstance(result, dict)
        self.assertEqual(result, {
            'audio': padded, 'caption': 'caption', 'lyrics': '[Instrumental]',
            'metadata': {'caption': 'caption', 'lyrics': '[Instrumental]', 'bpm': None,
                         'keyscale': '', 'timesignature': '', 'duration': 5.0,
                         'language': 'unknown', 'is_instrumental': True},
            'audio_path': 'temporary-sample.mp3',
        })
        self.torch.nn.functional.pad.assert_called_once_with(audio, (0, 192000))

    def test_raw_dataset_does_not_pad_longer_audio(self) -> None:
        result, audio = self._raw_item(480000)
        self.assertIsInstance(result, dict)
        if not isinstance(result, dict):
            raise AssertionError('missing raw audio result')
        self.assertIs(result['audio'], audio)
        self.torch.nn.functional.pad.assert_not_called()
