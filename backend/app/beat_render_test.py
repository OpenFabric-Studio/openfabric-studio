"""Real CPU FLOAT rendering and contained sample imports use temporary files."""
from __future__ import annotations

import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
import soundfile as sf

from app import beat_render, db
from app.beat_contracts import BeatError, BeatInstrumentManifest, BeatJobRequest, BeatStep
from app.beat_contracts_test import document_fixture, manifest_fixture


class BeatRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(db, 'FILES_DIR', self.root / 'files'))
        self.enterContext(patch.object(db, 'DATA_DIR', self.root))

    def kit(self, *, amplitude: float = 0.5) -> BeatInstrumentManifest:
        source = self.root / 'source.wav'
        samples = np.zeros(4801, dtype=np.float32)
        samples[0], samples[-1] = amplitude, amplitude / 2
        sf.write(source, samples, 48000, subtype='FLOAT')
        value = source.read_bytes()
        kit = manifest_fixture()
        region = kit.instruments[0].regions[0].model_copy(update={'sha256': hashlib.sha256(value).hexdigest(),
            'byte_size': len(value), 'sample_rate_hz': 48000, 'frames': len(samples), 'attack_ms': 0, 'release_ms': 1})
        kit = kit.model_copy(update={'instruments': [kit.instruments[0].model_copy(update={'regions': [region]})]})
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as zipped:
            zipped.writestr('sample.wav', value)
        archive.seek(0)
        return beat_render.import_kit(kit, archive).manifest

    def test_procedural_kit_has_real_hash_bound_samples_and_no_install_dependency(self) -> None:
        kit = beat_render.load_kit('procedural-core')
        self.assertEqual(len(kit.instruments), 8)
        for instrument in kit.instruments:
            for region in instrument.regions:
                path = beat_render.resolve_sample(kit, region.asset_id)
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), region.sha256)
                self.assertEqual(region.terms.scope, 'samples')
        self.assertEqual(kit, beat_render.load_kit('procedural-core'))

    def test_exact_float_stems_and_onsets_through_block_boundary(self) -> None:
        kit = self.kit()
        document = document_fixture(kit)
        document.patterns[0].lanes[0].steps[:] = [BeatStep(index=0, velocity=127),
            BeatStep(index=1, velocity=127, offset_steps=-1904 / 6000)]
        request = BeatJobRequest(beat_id=document.id, expected_revision=0, mode='full', submission_key='d' * 32)
        directory = beat_render.workspace('c' * 32, create=True) / 'output'
        manifest = beat_render.render(document, request, kit, 'c' * 32, directory)
        self.assertEqual((manifest.sample_rate_hz, manifest.channels, manifest.frames), (48000, 2, 96000))
        stems = [item for item in manifest.artifacts if item.kind == 'stem']
        self.assertEqual(len(stems), 8)
        arrays = [sf.read(directory / item.filename, dtype='float32', always_2d=True)[0] for item in stems]
        mix_artifact = next(item for item in manifest.artifacts if item.kind == 'mix')
        mix = sf.read(directory / mix_artifact.filename, dtype='float32', always_2d=True)[0]
        self.assertEqual(sf.info(directory / mix_artifact.filename).subtype, 'FLOAT')
        self.assertTrue(np.array_equal(np.sum(np.stack(arrays), axis=0, dtype=np.float32), mix))
        self.assertAlmostEqual(float(mix[0, 0]), 0.5 / np.sqrt(2), places=6)
        self.assertAlmostEqual(float(mix[4096, 0]), 0.5 / np.sqrt(2), places=6)
        self.assertAlmostEqual(float(mix[4800, 0]), 0.25 / np.sqrt(2), places=6)
        self.assertEqual(manifest.midi_error_code, 'beat_midi_percussion_overlap')
        self.assertTrue((directory / 'manifest.json').is_file())

    def test_float_peaks_are_preserved_without_clipping_or_hidden_limiting(self) -> None:
        kit = self.kit(amplitude=4)
        document = document_fixture(kit)
        document.patterns[0].lanes[0].steps[:] = [BeatStep(index=0, velocity=127)]
        request = BeatJobRequest(beat_id=document.id, expected_revision=0, mode='full', submission_key='d' * 32)
        directory = beat_render.workspace('e' * 32, create=True) / 'output'
        manifest = beat_render.render(document, request, kit, 'e' * 32, directory)
        mix = next(item for item in manifest.artifacts if item.kind == 'mix')
        audio = sf.read(directory / mix.filename, dtype='float32', always_2d=True)[0]
        self.assertGreater(float(np.max(np.abs(audio))), 1)
        self.assertEqual(manifest.master_gain, 1)
        self.assertIn('beat_peak_over_one', manifest.warnings)

    def test_owned_sample_resolver_uses_retained_bytes_and_keeps_hash_validation(self) -> None:
        kit = self.kit()
        document = document_fixture(kit)
        original = beat_render.resolve_sample(kit, 'sample')
        private = self.root / 'retained.wav'
        private.write_bytes(original.read_bytes())
        original.unlink()
        request = BeatJobRequest(beat_id=document.id, expected_revision=0, mode='full', submission_key='d' * 32)
        identifier = '5' * 32
        output = beat_render.workspace(identifier, create=True) / 'output'
        manifest = beat_render.render(document, request, kit, identifier, output,
            sample_resolver=lambda _kit, _asset: private)
        self.assertEqual(manifest.frames, 96000)
        private.write_bytes(b'changed')
        with self.assertRaises(BeatError):
            beat_render.render(document, request, kit, '6' * 32,
                beat_render.workspace('6' * 32, create=True) / 'output', sample_resolver=lambda _kit, _asset: private)

    def test_import_rejects_paths_links_changed_samples_and_preserves_existing_kit(self) -> None:
        kit = self.kit()
        original = beat_render.resolve_sample(kit, 'sample').read_bytes()
        for name, data, link in (('../sample.wav', original, False), ('sample.wav', b'changed', False), ('sample.wav', original, True)):
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, 'w') as zipped:
                info = zipfile.ZipInfo(name)
                if link:
                    info.create_system = 3
                    info.external_attr = (0o120777 << 16)
                zipped.writestr(info, data)
            archive.seek(0)
            with self.subTest(name=name, link=link), self.assertRaises(BeatError):
                beat_render.import_kit(kit, archive)
        self.assertEqual(beat_render.resolve_sample(kit, 'sample').read_bytes(), original)

    def test_import_rejects_excessive_zip_directory_before_allocating_metadata(self) -> None:
        kit = self.kit()
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as zipped:
            for index in range(1000):
                zipped.writestr(f'{index}.wav', b'')
        archive.seek(0)
        with patch.object(beat_render.zipfile, 'ZipFile', side_effect=AssertionError('unbounded directory allocated')):
            with self.assertRaises(BeatError):
                beat_render.import_kit(kit, archive)

    def test_rerender_has_identical_audio_bytes_and_pitch_keeps_last_sample(self) -> None:
        kit = self.kit()
        instrument = kit.instruments[0].model_copy(update={'pitched': True, 'supports_pitch_curve': True, 'role': 'pitched'})
        kit = kit.model_copy(update={'kit_id': 'pitched-kit', 'instruments': [instrument]})
        source = self.root / 'source.wav'
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as zipped:
            zipped.writestr('sample.wav', source.read_bytes())
        archive.seek(0)
        kit = beat_render.import_kit(kit, archive).manifest
        document = document_fixture(kit)
        document.patterns[0].lanes[0].steps[:] = [BeatStep(index=0, note_midi=72, velocity=127)]
        request = BeatJobRequest(beat_id=document.id, expected_revision=0, mode='full', submission_key='d' * 32)
        hashes = []
        for render_id in ('1' * 32, '2' * 32):
            directory = beat_render.workspace(render_id, create=True) / 'output'
            result = beat_render.render(document, request, kit, render_id, directory)
            mix = next(item for item in result.artifacts if item.kind == 'mix')
            hashes.append(mix.sha256)
            audio = sf.read(directory / mix.filename, dtype='float32', always_2d=True)[0]
            self.assertAlmostEqual(float(audio[2400, 0]), .25 / np.sqrt(2), places=6)
            self.assertEqual(float(audio[2401, 0]), 0)
            raw = (directory / mix.filename).read_bytes()
            peak = raw.index(b'PEAK')
            self.assertEqual(raw[peak + 12:peak + 16], b'\0' * 4)
        self.assertEqual(hashes[0], hashes[1])

    def test_scoped_sample_restrictions_and_public_attribution_survive_without_private_notes(self) -> None:
        kit = self.kit()
        region = kit.instruments[0].regions[0]
        terms = region.terms.model_copy(update={'component_id': '/Users/private/operator',
            'restriction': 'noncommercial', 'notes': 'personal release notes: secret-token'})
        region = region.model_copy(update={'terms': terms, 'attribution': 'Copyright Example Music; public attribution required.'})
        kit = kit.model_copy(update={'instruments': [kit.instruments[0].model_copy(update={'regions': [region]})]})
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as zipped:
            zipped.writestr('sample.wav', (self.root / 'source.wav').read_bytes())
        archive.seek(0)
        kit = beat_render.import_kit(kit, archive).manifest
        document = document_fixture(kit)
        request = BeatJobRequest(beat_id=document.id, expected_revision=0, mode='full', submission_key='d' * 32)
        directory = beat_render.workspace('3' * 32, create=True) / 'output'
        result = beat_render.render(document, request, kit, '3' * 32, directory)
        proof = next(item for item in result.artifacts if item.kind == 'provenance' and item.filename.endswith('.json'))
        shared = (directory / proof.filename).read_text()
        self.assertIn('noncommercial', shared)
        self.assertIn('Copyright Example Music', shared)
        self.assertNotIn('/Users/private', shared)
        self.assertNotIn('secret-token', shared)
        bundle = next(item for item in result.artifacts if item.kind == 'bundle')
        with zipfile.ZipFile(directory / bundle.filename) as zipped:
            self.assertNotIn('sample.wav', zipped.namelist())

    def test_gated_sample_release_follows_source_grid_gate_without_extending_one_shot(self) -> None:
        kit = self.kit()
        instrument = kit.instruments[0]
        region = instrument.regions[0].model_copy(update={'one_shot': False})
        kit = kit.model_copy(update={'instruments': [instrument.model_copy(update={'regions': [region]})]})
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as zipped:
            zipped.writestr('sample.wav', (self.root / 'source.wav').read_bytes())
        archive.seek(0)
        kit = beat_render.import_kit(kit, archive).manifest
        document = document_fixture(kit)
        document.patterns[0].lanes[0].steps[:] = [BeatStep(index=0, gate_steps=.05, velocity=127)]
        request = BeatJobRequest(beat_id=document.id, expected_revision=0, mode='full', submission_key='d' * 32)
        directory = beat_render.workspace('4' * 32, create=True) / 'output'
        result = beat_render.render(document, request, kit, '4' * 32, directory)
        mix = next(item for item in result.artifacts if item.kind == 'mix')
        audio = sf.read(directory / mix.filename, dtype='float32', always_2d=True)[0]
        self.assertGreater(float(audio[0, 0]), 0)
        self.assertEqual(float(audio[4800, 0]), 0)

    def test_cancelled_render_has_no_published_manifest_and_keeps_source(self) -> None:
        kit = self.kit()
        document = document_fixture(kit)
        request = BeatJobRequest(beat_id=document.id, expected_revision=0, mode='preview',
            preview_pattern_id='A', submission_key='d' * 32)
        directory = beat_render.workspace('f' * 32, create=True) / 'output'
        calls = 0
        def cancelled() -> bool:
            nonlocal calls
            calls += 1
            return calls > 2
        with self.assertRaises(BeatError) as caught:
            beat_render.render(document, request, kit, 'f' * 32, directory, cancelled=cancelled)
        self.assertEqual(caught.exception.code, 'beat_cancelled')
        self.assertFalse((directory / 'manifest.json').exists())
        self.assertTrue(beat_render.resolve_sample(kit, 'sample').is_file())

    def test_output_symlink_cannot_truncate_a_file_outside_the_managed_render(self) -> None:
        kit = self.kit()
        document = document_fixture(kit)
        request = BeatJobRequest(beat_id=document.id, expected_revision=0, mode='full', submission_key='d' * 32)
        outside = self.root / 'precious.wav'
        original = (self.root / 'source.wav').read_bytes()
        outside.write_bytes(original)
        directory = beat_render.workspace('5' * 32, create=True) / 'output'
        directory.mkdir()
        (directory / 'mix.partial.wav').symlink_to(outside)
        with self.assertRaises((BeatError, OSError)):
            beat_render.render(document, request, kit, '5' * 32, directory)
        self.assertEqual(outside.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
