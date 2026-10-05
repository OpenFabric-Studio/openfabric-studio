"""Comparison drafts use held-out images and reviewed results bind actual media."""
from __future__ import annotations
import tempfile
import asyncio
import sys
import subprocess
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock
from PIL import Image
from app import video_character_training as training, video_projects as store
from app.video_contracts import CharacterDatasetItem, CharacterDatasetReview, VideoCharacterTrainingJob, VideoVariant, ReviewCharacterAdapterRequest
from app.video_training_review import build_provenance


class CharacterComparisonTests(unittest.IsolatedAsyncioTestCase):
    async def test_comparison_uses_only_held_out_reference_and_rejects_changed_image(self) -> None:
        from app import video_character_comparison as comparison
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            with patch.object(training,'DATA_DIR',root),patch.object(store,'DATA_DIR',root), patch('app.video_render._engine_fingerprint',new=AsyncMock(return_value='e'*64)):
                job_id='a'*32; job_root=training.job_dir(job_id);job_root.mkdir(parents=True)
                paths=[]
                for index,color in enumerate(('red','green','blue','yellow')):
                    path=job_root/f'{index}.png';Image.new('RGB',(32,32),color).save(path);paths.append(path)
                review=CharacterDatasetReview(reviewed=True,items=[CharacterDatasetItem(upload_index=index,caption=f'View{index}',role='held_out' if index==3 else 'training') for index in range(4)])
                provenance=build_provenance(job_root,paths,['photo']*4,review)
                adapter=training._artifact(job_id,'adapter/adapter.safetensors');adapter.parent.mkdir();adapter.write_bytes(b'lora'*400)
                training._save(VideoCharacterTrainingJob(id=job_id,name='Actor',status='completed',consent_confirmed=True,photo_count=3,clip_count=0,adapter_ready=True,provenance=provenance,created_at='',updated_at=''))
                job=comparison.create(job_id)
                self.assertIsNotNone(job.comparison)
                record=job.comparison
                if record is None:raise AssertionError('Missing comparison')
                for project_id in (record.baseline_project_id,record.adapted_project_id):
                    project=store.get(project_id)
                    reference=store.reference_file(project_id,project.references[0].id)
                    self.assertEqual(reference.read_bytes(),paths[3].read_bytes())
                    document=store.load(project_id)
                    for index,shot in enumerate(document.project.shots):
                        identifier=f'{index+1:032x}'
                        shot.variants=[VideoVariant(id=identifier,seed=shot.seed,status='ready',created_at='',prompt=shot.prompt,settings=project.settings)]
                        shot.approved_variant_id=identifier
                    document.project.file_url='/finished'
                    store.save(document)
                with self.assertRaisesRegex(store.VideoProjectError,'character_comparison_required'):
                    await comparison.review(job_id,ReviewCharacterAdapterRequest(comparison_id=record.id,reviewed=True,notes='Missing real media cannot count as reviewed'))
                from app import video_render as render
                template=root/'synthetic.mp4'
                subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=green:s=704x448:r=24','-t','4','-c:v','libx264','-pix_fmt','yuv420p',str(template)],check=True)
                for project_id in (record.baseline_project_id,record.adapted_project_id):
                    saved=store.load(project_id)
                    for shot in saved.project.shots:
                        variant=shot.variants[0]
                        variant.engine_fingerprint='e'*64
                        variant.fingerprint=render.fingerprint(saved,shot,variant.seed,'e'*64)
                        variant.reference_id=shot.reference_id
                        variant.reference_strength=shot.reference_strength
                        path=render.variant_path(project_id,shot.id,variant.id)
                        path.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(template,path)
                        render._receipt_path(path).write_text(render.VariantReceipt(variant_id=variant.id,shot_id=shot.id,fingerprint=variant.fingerprint,output_sha256=store.file_hash(path),seconds=4,width=704,height=448).model_dump_json())
                    store.save(saved)
                reviewed=await comparison.review(job_id,ReviewCharacterAdapterRequest(comparison_id=record.id,reviewed=True,notes='Synthetic media validates receipts; no GPU quality claim'))
                self.assertTrue(reviewed.provenance.evaluated)
                self.assertEqual(len(reviewed.comparison.reviewed_media_sha256),6)
                from app.job_lifecycle import spawn_process, communicate_process
                entered=asyncio.Event()
                processes: list[asyncio.subprocess.Process] = []
                async def blocking_validation(path: Path, seconds: float, size: tuple[int,int]) -> None:
                    proc=await spawn_process(sys.executable,'-c','import time; time.sleep(30)',stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
                    processes.append(proc);entered.set()
                    await communicate_process(proc,120)
                with patch.object(render,'validate_media',side_effect=blocking_validation):
                    task=asyncio.create_task(comparison.review(job_id,ReviewCharacterAdapterRequest(comparison_id=record.id,reviewed=True,notes='Must never publish during cancellation')))
                    async with asyncio.timeout(3):
                        await entered.wait()
                    self.assertTrue(comparison.work_busy())
                    await comparison.shutdown()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    self.assertFalse(comparison.work_busy())
                    self.assertTrue(all(proc.returncode is not None for proc in processes))
                    await comparison.recover()
                self.assertEqual(training.get_job(job_id).provenance.evaluation_notes,reviewed.provenance.evaluation_notes)
                # A changed adapter retains its job ID but invalidates every
                # old adapted render fingerprint. A human cannot review it as
                # though those frames were made by the changed weights.
                original_adapter=adapter.read_bytes();adapter.write_bytes(b'changed adapter'*400)
                with self.assertRaisesRegex(store.VideoProjectError,'character_comparison_required'):
                    await comparison.review(job_id,ReviewCharacterAdapterRequest(comparison_id=record.id,reviewed=True,notes='Changed adapter'))
                adapter.write_bytes(original_adapter)
                baseline_document=store.load(record.baseline_project_id)
                baseline_document.project.settings.cfg_scale=4
                for shot in baseline_document.project.shots:
                    shot.variants[0].settings=baseline_document.project.settings.model_copy()
                store.save(baseline_document)
                with self.assertRaisesRegex(store.VideoProjectError,'character_comparison_required'):
                    await comparison.review(job_id,ReviewCharacterAdapterRequest(comparison_id=record.id,reviewed=True,notes='Different CFG invalidates comparison'))
                baseline_document.project.settings.cfg_scale=3
                for shot in baseline_document.project.shots:
                    shot.variants[0].settings=baseline_document.project.settings.model_copy()
                store.save(baseline_document)
                baseline=store.get(record.baseline_project_id)
                reference=store.reference_file(baseline.id,baseline.references[0].id)
                Image.new('RGB',(32,32),'black').save(reference)
                with self.assertRaisesRegex(store.VideoProjectError,'character_comparison_required'):
                    await comparison.review(job_id,ReviewCharacterAdapterRequest(comparison_id=record.id,reviewed=True,notes='Compared the fixed prompts'))
                self.assertEqual(training.get_job(job_id).provenance.evaluation_notes,'Synthetic media validates receipts; no GPU quality claim')
