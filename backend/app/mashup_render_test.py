from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

from app import db, mashup_render
from app.mashup_contracts import MashupError, MashupGeneratePlanRequest, MashupJob, MashupJobRequest, MashupProgress, MashupStemSnapshot
from app.mashup_plan import generate
from app.mashup_plan_test import source_fixture, state_fixture
from app.video_process import run_owned_command


async def command(argv: list[str], timeout: float) -> bytes:
    result = await run_owned_command(argv, timeout=timeout, stderr=-2)
    if result.returncode != 0:
        raise RuntimeError('fixture command failed')
    return result.stdout


class MashupRenderTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.enterContext(patch.object(db, 'FILES_DIR', self.root))
        state = state_fixture().model_copy(update={'source_a': source_fixture('a', bars=(1,)),
            'source_b': source_fixture('b', bars=(1,))})
        sources = []
        for source in (state.source_a, state.source_b):
            folder = mashup_render.source_dir(state.id, source.side, create=True)
            prepared = source.prepared
            if prepared is None:
                self.fail('fixture')
            phase = np.arange(prepared.frames, dtype=np.float32) / 48000
            stems = []
            for index, stem in enumerate(('vocals', 'drums', 'bass', 'other')):
                signal = .03 * np.sin(2 * np.pi * (110 + 110 * index) * phase)
                path = folder / (stem + '.wav')
                sf.write(path, np.column_stack((signal, -signal)), 48000, subtype='FLOAT')
                fact = await mashup_render.audio_artifact(path)
                stems.append(MashupStemSnapshot.model_validate({'stem': stem, 'artifact': fact.model_dump()}))
            sources.append(source.model_copy(update={'stems': stems}))
        self.state = state.model_copy(update={'source_a': sources[0], 'source_b': sources[1]})
        plan = generate(self.state, MashupGeneratePlanRequest(expected_revision=0, orientation='a_vocals_b_backing',
            target_bpm=120, meter={'numerator': 4, 'denominator': 4}))
        self.state = self.state.model_copy(update={'plan': plan, 'revision': 1})

    async def test_configured_parent_alias_is_normalized_but_private_root_symlinks_are_rejected(self) -> None:
        alias = self.root / 'parent-alias'
        alias.symlink_to(self.root, target_is_directory=True)
        with patch.object(db, 'FILES_DIR', alias / 'files'):
            self.assertEqual(mashup_render.root(), self.root / 'files' / '_mashups')
            target = self.root / 'elsewhere'
            target.mkdir()
            (self.root / 'files' / '_mashups').rmdir()
            (self.root / 'files' / '_mashups').symlink_to(target, target_is_directory=True)
            with self.assertRaises(MashupError):
                mashup_render.root()

    async def test_real_identity_dsp_renders_four_aligned_stems_and_editable_placements(self) -> None:
        request = MashupJobRequest(expected_revision=1, submission_key='render')
        job = MashupJob(id='d' * 32, mashup_id=self.state.id, mashup_revision=1, kind='full', status='running',
            request=request, progress=MashupProgress(phase='transforming'), created_at=self.state.created_at)
        output = mashup_render.job_dir(job.id, create=True) / 'output'
        manifest = await mashup_render.render(self.state, job, output, command, native=None)
        self.assertEqual((manifest.frames, manifest.sample_rate_hz, manifest.channels), (96000, 48000, 2))
        stems = [item for item in manifest.artifacts if item.kind == 'stem']
        self.assertEqual(len(stems), 4)
        self.assertEqual(len(manifest.placements), 4)
        mix_artifact = next(item for item in manifest.artifacts if item.kind == 'mix')
        mix, _ = sf.read(output / mix_artifact.filename, dtype='float32', always_2d=True)
        total = np.zeros_like(mix)
        for item in stems:
            samples, _ = sf.read(output / item.filename, dtype='float32', always_2d=True)
            self.assertEqual(samples.shape, mix.shape)
            total += samples
        np.testing.assert_allclose(mix, total, atol=2e-8)
        self.assertEqual(mashup_render.validate_manifest(job.id), manifest)
        self.assertNotIn(str(self.root), manifest.model_dump_json())
        self.assertFalse(manifest.music_evidence.lineage_complete)
        self.assertEqual(manifest.metrics.source_sha256, mix_artifact.sha256)
        preview_request = MashupJobRequest(expected_revision=1, submission_key='matching-preview', mode='preview',
            preview_start_tick=960, preview_end_tick=2880)
        preview_job = job.model_copy(update={'id': 'f' * 32, 'kind': 'preview', 'request': preview_request})
        preview_output = mashup_render.job_dir(preview_job.id, create=True) / 'output'
        preview = await mashup_render.render(self.state, preview_job, preview_output, command, native=None)
        preview_mix = next(item for item in preview.artifacts if item.kind == 'mix')
        preview_samples, _ = sf.read(preview_output / preview_mix.filename, dtype='float32', always_2d=True)
        np.testing.assert_allclose(preview_samples, mix[24000:72000], atol=2e-8)
        target = output / mix_artifact.filename
        target.write_bytes(b'corrupt')
        with self.assertRaises(MashupError):
            mashup_render.validate_manifest(job.id)

    async def test_forecast_and_preview_use_selected_ticks_and_detect_changed_stem(self) -> None:
        request = MashupJobRequest(expected_revision=1, submission_key='preview', mode='preview',
            preview_start_tick=960, preview_end_tick=2880)
        forecast = mashup_render.forecast(self.state, request, native_ready=False)
        self.assertEqual((forecast.frames, forecast.duration_sec), (48000, 1))
        self.assertTrue(forecast.stretch_ready)
        source = self.state.source_a.stems[0]
        path = mashup_render.source_dir(self.state.id, 'a') / source.artifact.filename
        path.write_bytes(b'changed')
        job = MashupJob(id='e' * 32, mashup_id=self.state.id, mashup_revision=1, kind='preview', status='running',
            request=request, progress=MashupProgress(phase='transforming'), created_at=self.state.created_at)
        with self.assertRaises(MashupError):
            await mashup_render.render(self.state, job, mashup_render.job_dir(job.id, create=True) / 'output', command, native=None)


if __name__ == '__main__':
    unittest.main()
