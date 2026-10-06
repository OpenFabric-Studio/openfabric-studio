"""Measured exports distinguish inter-sample peaks and keep accepted sources dry."""
from __future__ import annotations

import asyncio
import hashlib
import math
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from app import audio_exports as exports, db
from app.audio_encoding import AudioEncodingSettings


class AudioExportQualityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.enterContext(patch.object(db,'FILES_DIR',self.root/'files'))
        self.enterContext(patch.object(db,'DB_PATH',self.root/'catalog.db'))
        self.enterContext(patch.object(db,'_db',None))
        self.enterContext(patch.object(exports,'_capacity',asyncio.Semaphore(2)))
        self.enterContext(patch.object(exports,'_lock',asyncio.Lock()))
        self.source=self.root/'files'/'source.wav';self.source.parent.mkdir()
        self.write_tone(1.25,12000,phase=math.pi/4)
        cursor=db.get_db().execute("INSERT INTO tracks(model,created_at,title,lyrics,params_json,audio_path) VALUES('ace_step','2026-10-06','Fixture','','{}',?)",(str(self.source),))
        db.get_db().commit()
        if cursor.lastrowid is None:raise AssertionError('missing fixture track')
        self.track_id=cursor.lastrowid;self.version_id='a'*32
        self.enterContext(patch.object(exports,'resolve_source',return_value=self.source))

    def write_tone(self, amplitude: float, frequency: int, *, phase: float=0) -> None:
        samples=(int(max(-32768,min(32767,amplitude*math.sin(2*math.pi*frequency*index/48000+phase)*32767))) for index in range(48000*3))
        with wave.open(str(self.source),'wb') as output:
            output.setnchannels(1);output.setsampwidth(2);output.setframerate(48000)
            output.writeframes(b''.join(struct.pack('<h',value) for value in samples))

    async def asyncTearDown(self) -> None:
        await exports.shutdown_exports()
        if db._db is not None:db._db.close()

    async def finish(self, identifier: str) -> exports.AudioExportResponse:
        for _ in range(1000):
            result=exports.get_export(self.track_id,self.version_id,identifier)
            if result.status not in {'queued','running'}:return result
            await asyncio.sleep(.01)
        self.fail('export did not finish')

    async def test_editor_generated_ancestry_does_not_hide_an_untracked_source(self) -> None:
        from app import export_provenance
        parent=db.insert_track(model='ace_step',title='Source',lyrics='',seed=1,duration_ms=3000,wall_ms=1,params={},audio_path=self.source,abc_path=None)
        import json
        params={'source_track_ids':[parent],'source_ancestry_complete':False}
        db.get_db().execute("UPDATE tracks SET model='editor',params_json=? WHERE id=?",(json.dumps(params),self.track_id));db.get_db().commit()
        captured=export_provenance.track_component(self.track_id,self.version_id,'0'*64)
        self.assertEqual(captured.content_origin,'mixed')

    async def test_unverified_measurement_worker_stops_export_before_another_command(self) -> None:
        from unittest.mock import AsyncMock
        from app import audio_quality
        with patch.object(audio_quality,'measure',new=AsyncMock(side_effect=exports.AudioExportError('worker_identity_unverified'))):
            job=await exports.create_export(self.track_id,self.version_id,'mp3')
            result=await self.finish(job.id)
        self.assertEqual(result.status,'failed')
        self.assertEqual(result.error_code,'worker_identity_unverified')
        self.assertIsNone(result.audio_url)
        self.assertFalse(exports._artifact(exports.load_document(self.track_id,job.id)).exists())

    async def test_intersample_peak_is_measured_even_when_no_samples_clip(self) -> None:
        original=hashlib.sha256(self.source.read_bytes()).hexdigest()
        job=await exports.create_export(self.track_id,self.version_id,'wav')
        result=await self.finish(job.id)
        self.assertEqual(result.status,'done',result.error_code)
        measured=getattr(result,'input_metrics',None)
        self.assertIsNotNone(measured,'durable exports must report measured input diagnostics')
        if measured is None:return
        self.assertLess(measured.sample_peak_dbfs,-.5)
        self.assertGreater(measured.true_peak_dbtp,.5)
        self.assertEqual(measured.full_scale_fraction,0)
        self.assertIn('true_peak_over',measured.warnings)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(),original)

    async def test_explicit_spoken_target_is_verified_after_codec_and_preserves_dry_source(self) -> None:
        self.write_tone(.2,330)
        original=self.source.read_bytes()
        settings=AudioEncodingSettings.model_validate({'loudness':{'profile':'spoken_word'}})
        job=await exports.create_export(self.track_id,self.version_id,'mp3',settings)
        result=await self.finish(job.id)
        self.assertEqual(result.status,'done',result.error_code)
        measured=getattr(result,'output_metrics',None)
        self.assertIsNotNone(measured)
        if measured is None:return
        self.assertAlmostEqual(measured.integrated_lufs,-16,delta=.5)
        self.assertLessEqual(measured.true_peak_dbtp,-1.9)
        self.assertEqual(result.target_result,'met')
        self.assertEqual(getattr(result.provenance,'audio_target_result',None),'met')
        self.assertEqual(getattr(result.provenance,'audio_target',None),settings.loudness)
        self.assertEqual(self.source.read_bytes(),original)
        self.assertEqual(exports.get_export(self.track_id,self.version_id,job.id).output_metrics,measured)

    async def test_clipped_samples_and_measurement_identity_are_retained(self) -> None:
        self.write_tone(1.5,1000)
        job=await exports.create_export(self.track_id,self.version_id,'wav',operation='analyze')
        result=await self.finish(job.id)
        self.assertEqual(result.status,'done',result.error_code)
        measured=result.input_metrics
        if measured is None:raise AssertionError('missing measured clipped waveform')
        self.assertGreater(measured.full_scale_fraction,.3)
        self.assertIn('full_scale_samples',measured.warnings)
        self.assertTrue(measured.ffmpeg_version.startswith('ffmpeg version '))
        self.assertEqual(measured.source_sha256,hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(measured.samples_analyzed,144000)
        self.assertEqual(measured.measurement_method,'ffmpeg-loudnorm-oversampled-v1')

    async def test_silence_has_null_loudness_and_inconclusive_target(self) -> None:
        self.write_tone(0,330)
        settings=AudioEncodingSettings.model_validate({'loudness':{'profile':'spoken_word'}})
        job=await exports.create_export(self.track_id,self.version_id,'wav',settings)
        result=await self.finish(job.id)
        self.assertEqual(result.status,'done',result.error_code)
        self.assertEqual(result.target_result,'inconclusive')
        self.assertIsNone(result.output_metrics.integrated_lufs)
        self.assertIn('silence',result.output_metrics.warnings)

    async def test_recovery_keeps_canceled_analysis_terminal_even_with_captured_measurements(self) -> None:
        job=await exports.create_export(self.track_id,self.version_id,'wav',operation='analyze')
        result=await self.finish(job.id)
        self.assertEqual(result.status,'done')
        document=exports.load_document(self.track_id,job.id)
        document.status='cancelled';document.error_code='cancelled';exports.save_document(document)
        await exports.recover_exports()
        self.assertEqual(exports.get_export(self.track_id,self.version_id,job.id).status,'cancelled')

    async def test_analysis_is_durable_without_publishing_or_promoting_an_audio_file(self) -> None:
        job=await exports.create_export(self.track_id,self.version_id,'wav',operation='analyze')
        result=await self.finish(job.id)
        self.assertEqual(result.status,'done');self.assertEqual(result.operation,'analyze')
        self.assertIsNone(result.audio_url);self.assertIsNotNone(result.input_metrics)
        await exports.recover_exports()
        self.assertEqual(exports.get_export(self.track_id,self.version_id,job.id).input_metrics,result.input_metrics)
        self.assertEqual(db.get_track(self.track_id)['audio_path'],str(self.source))
        with self.assertRaises(exports.AudioExportError):exports.export_file(self.track_id,self.version_id,job.id)

    async def test_export_manifest_binds_exact_media_and_does_not_mislabel_imported_recordings(self) -> None:
        job=await exports.create_export(self.track_id,self.version_id,'flac')
        result=await self.finish(job.id)
        provenance=getattr(result,'provenance',None)
        self.assertIsNotNone(provenance,'every completed export needs a hash-bound manifest')
        if provenance is None:return
        self.assertEqual(provenance.content_origin,'generated')
        path=exports.export_file(self.track_id,self.version_id,job.id)
        self.assertEqual(provenance.artifact_sha256,hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(exports.manifest_file(self.track_id,self.version_id,job.id,'json').read_text(),provenance.model_dump_json(indent=2)+'\n')
        self.assertIn('generated',exports.manifest_file(self.track_id,self.version_id,job.id,'txt').read_text())
        db.get_db().execute("UPDATE tracks SET model='upload' WHERE id=?",(self.track_id,));db.get_db().commit()
        fresh=await exports.create_export(self.track_id,self.version_id,'mp3')
        imported=await self.finish(fresh.id)
        self.assertEqual(imported.provenance.content_origin,'unknown')
        self.assertEqual(imported.provenance.components[0].content_origin,'unknown')


if __name__=='__main__':unittest.main()
