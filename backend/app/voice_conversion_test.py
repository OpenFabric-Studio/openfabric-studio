from pathlib import Path
import unittest

from app.voice_conversion import seed_command


class SharedConversionCommandTests(unittest.TestCase):
    def test_seed_command_preserves_legacy_inference_flags_and_explicit_pinned_files(self) -> None:
        command = seed_command(Path('/private/python'), Path('/private/source.wav'),
            Path('/private/reference.wav'), Path('/private/output'), diffusion_steps=30,
            checkpoint=Path('/private/model.pth'), configuration=Path('/private/config.yml'))
        self.assertEqual(command[:2], ['/private/python', 'inference.py'])
        self.assertEqual(command[command.index('--f0-condition') + 1], 'True')
        self.assertEqual(command[command.index('--auto-f0-adjust') + 1], 'False')
        self.assertEqual(command[command.index('--fp16') + 1], 'False')
        self.assertEqual(command[-4:], ['--checkpoint', '/private/model.pth', '--config', '/private/config.yml'])

    def test_missing_one_pinned_file_is_rejected_instead_of_silently_using_base_weights(self) -> None:
        with self.assertRaises(ValueError):
            seed_command(Path('/python'), Path('/source'), Path('/reference'), Path('/output'),
                diffusion_steps=30, checkpoint=Path('/model'), configuration=None)

    def test_bounded_mono_preserves_antiphase_energy_and_exact_frames(self) -> None:
        import tempfile
        import numpy as np
        import soundfile as sf
        from app.voice_conversion import phase_safe_mono_file
        with tempfile.TemporaryDirectory() as temporary:
            source, output = Path(temporary) / 'source.wav', Path(temporary) / 'mono.wav'
            signal = np.asarray(np.sin(np.arange(150000) * .1) * 1.5, dtype=np.float32)
            sf.write(source, np.column_stack((signal, -signal)), 48000, subtype='FLOAT')
            original = source.read_bytes()
            phase_safe_mono_file(source, output)
            actual, rate = sf.read(output, dtype='float32')
            self.assertEqual(rate, 48000)
            np.testing.assert_array_equal(actual, signal)
            self.assertEqual(source.read_bytes(), original)
