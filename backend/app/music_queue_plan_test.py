"""Common music briefs preserve intent while exposing engine limitations."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from app.music_lora_contracts import MusicAdapter, MusicAdapterSelection, MusicLoraError
from app.music_queue_contracts import (
    MusicQueueBrief, MusicQueueCatalog, MusicQueueEngineSelection, MusicQueueEnqueueRequest, MusicQueuePreviewRequest,
    MusicQueueReadiness,
)
from app import music_queue_plan
from app.module_catalog import ModuleEnvironment
from app.module_runtime import RuntimeEvidence
from app.orchestrator.state import ModelRuntimeState, ModelStatus


def ready_catalog() -> MusicQueueCatalog:
    return MusicQueueCatalog(engines=[MusicQueueReadiness(engine=engine, supported=True,
        installed=True, weights_ready=True, managed_status='stopped', ready=True)
        for engine in ('ace_step', 'yue2', 'stable_audio3')], adapters=[])


class MusicQueueContractTests(unittest.TestCase):
    def test_brief_and_selections_reject_unbounded_or_inconsistent_input(self) -> None:
        for update in ({'description': ' '}, {'title': 'x' * 121}, {'description': 'x' * 3501},
                       {'lyrics': 'x' * 20001}, {'duration_seconds': 9}, {'duration_seconds': 601},
                       {'duration_seconds': True}, {'bpm': 19}, {'bpm': 301}, {'bpm': True},
                       {'key': 'x' * 81}, {'language': 'x' * 81}, {'instrumental': True, 'lyrics': 'Keep me'},
                       {'output_path': '/tmp/unsafe.wav'}):
            with self.subTest(update=update), self.assertRaises(ValidationError):
                MusicQueueBrief.model_validate({'description': 'Jazz', 'duration_seconds': 95, **update})
        with self.assertRaises(ValidationError):
            MusicQueuePreviewRequest(brief=MusicQueueBrief(description='Jazz', duration_seconds=95),
                selections=[MusicQueueEngineSelection(engine='ace_step'), MusicQueueEngineSelection(engine='ace_step')])
        for value in (0, 9, True):
            with self.subTest(variations=value), self.assertRaises(ValidationError):
                MusicQueueEngineSelection.model_validate({'engine': 'ace_step', 'variations': value})
        for value in ({'engine': 'heartmula', 'variant': 'medium'},
                      {'engine': 'ace_step', 'adapter': {'id': 'a' * 32}},
                      {'engine': 'stable_audio3', 'ar_adapter': {'id': 'a' * 32}}):
            with self.subTest(selection=value), self.assertRaises(ValidationError):
                MusicQueueEngineSelection.model_validate(value)


class MusicQueuePlanTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.ready = patch.object(music_queue_plan, 'catalog', new=AsyncMock(return_value=ready_catalog()))
        self.ready.start()
        self.weights_ready = patch.object(music_queue_plan, '_weights_ready', return_value=True)
        self.weights_ready.start()

    async def asyncTearDown(self) -> None:
        self.weights_ready.stop()
        self.ready.stop()

    def request(self, *selections: MusicQueueEngineSelection, **changes: object) -> MusicQueuePreviewRequest:
        brief = MusicQueueBrief.model_validate({'title': 'Original title', 'description': 'Warm jazz',
            'lyrics': '[Verse]\nKeep every word.', 'duration_seconds': 350, 'bpm': 88,
            'key': 'D minor', 'language': 'English', **changes})
        return MusicQueuePreviewRequest(brief=brief, selections=list(selections))

    async def test_two_models_and_three_variations_are_ordered_typed_requests(self) -> None:
        request = self.request(MusicQueueEngineSelection(engine='ace_step', variations=3),
            MusicQueueEngineSelection(engine='yue2', variations=3))
        result = await music_queue_plan.preview(request)
        self.assertTrue(result.can_enqueue)
        self.assertEqual([item.engine for item in result.items], ['ace_step'] * 3 + ['yue2'] * 3)
        self.assertEqual([item.variation_index for item in result.items], [1, 2, 3, 1, 2, 3])
        self.assertEqual(result.total_items, 6)
        self.assertEqual(result.brief, request.brief)
        ace = result.items[0].payload
        yue = result.items[3].payload
        self.assertEqual(ace.engine, 'ace_step')
        self.assertEqual(yue.engine, 'yue2')
        if ace.engine != 'ace_step' or yue.engine != 'yue2':
            self.fail('Unexpected typed engine wrapper')
        self.assertEqual(ace.request.customLyrics, request.brief.lyrics)
        self.assertEqual(ace.request.customPrompt, request.brief.description)
        self.assertFalse(ace.request.durationAuto)
        self.assertFalse(ace.request.randomSeed)
        self.assertFalse(ace.request.useCotCaption)
        self.assertEqual(ace.request.batchSize, 1)
        self.assertEqual(ace.request.duration, 300)
        self.assertEqual(yue.request.lyrics, request.brief.lyrics)
        self.assertIn('Tempo: 88 BPM', yue.request.options.style)
        self.assertIn('Key: D minor', yue.request.options.style)
        self.assertIn('Language: English', yue.request.options.style)
        self.assertIsNone(result.models[1].effective_duration_seconds)
        self.assertEqual(result.models[1].duration_mode, 'model_led')
        self.assertTrue(result.models[0].adjustments)

    async def test_incompatible_vocals_and_missing_yue_lyrics_remain_visible(self) -> None:
        result = await music_queue_plan.preview(self.request(MusicQueueEngineSelection(engine='stable_audio3')))
        self.assertFalse(result.can_enqueue)
        self.assertEqual(result.models[0].error_code, 'stable_audio3_instrumental_only')
        self.assertEqual(result.brief.lyrics, '[Verse]\nKeep every word.')
        self.assertEqual(result.items, [])
        for changes in ({'lyrics': ''}, {'instrumental': True, 'lyrics': ''}):
            result = await music_queue_plan.preview(self.request(MusicQueueEngineSelection(engine='yue2'), **changes))
            self.assertFalse(result.can_enqueue)
            self.assertIn(result.models[0].error_code, ('music_queue_yue_lyrics_required', 'music_queue_yue_vocals_required'))

    async def test_local_duration_limits_are_visible(self) -> None:
        for variant, seconds in (('small-music', 120), ('medium', 350)):
            request = self.request(MusicQueueEngineSelection.model_validate({'engine': 'stable_audio3', 'variant': variant}),
                instrumental=True, lyrics='')
            result = await music_queue_plan.preview(request)
            self.assertTrue(result.can_enqueue)
            self.assertEqual(result.models[0].effective_duration_seconds, seconds)
            self.assertEqual(result.items[0].payload.request.seed, 0)

    async def test_six_hundred_second_brief_preserves_each_engine_duration_limit(self) -> None:
        for variant, stable_limit in (('small-music', 120), ('medium', 380)):
            with self.subTest(variant=variant):
                result = await music_queue_plan.preview(self.request(
                    MusicQueueEngineSelection(engine='ace_step'),
                    MusicQueueEngineSelection.model_validate({'engine': 'stable_audio3', 'variant': variant}),
                    duration_seconds=600, instrumental=True, lyrics=''))
                self.assertTrue(result.can_enqueue)
                self.assertEqual(result.brief.duration_seconds, 600)
                self.assertEqual([model.effective_duration_seconds for model in result.models], [300, stable_limit])
                ace, stable = (item.payload for item in result.items)
                if ace.engine != 'ace_step' or stable.engine != 'stable_audio3':
                    self.fail('Unexpected engine payload')
                self.assertEqual(ace.request.duration, 300)
                self.assertEqual(stable.request.duration_seconds, stable_limit)

    async def test_assembled_prompt_overflow_is_rejected_without_truncation(self) -> None:
        with patch.object(music_queue_plan, '_directions', return_value='x' * 4001):
            result = await music_queue_plan.preview(self.request(MusicQueueEngineSelection(engine='stable_audio3'), instrumental=True, lyrics=''))
        self.assertFalse(result.can_enqueue)
        self.assertEqual(result.models[0].error_code, 'music_queue_prompt_too_long')
        self.assertEqual(result.brief.description, 'Warm jazz')

    async def test_missing_setup_keeps_normalized_preview_but_blocks_enqueue(self) -> None:
        state = ready_catalog()
        state.engines[0].ready = False
        state.engines[0].installed = False
        state.engines[0].error_code = 'music_queue_engine_not_installed'
        with patch.object(music_queue_plan, 'catalog', new=AsyncMock(return_value=state)):
            result = await music_queue_plan.preview(self.request(MusicQueueEngineSelection(engine='ace_step')))
        self.assertFalse(result.can_enqueue)
        self.assertEqual(len(result.items), 1)
        self.assertTrue(result.models[0].compatible)
        self.assertEqual(result.models[0].readiness.error_code, 'music_queue_engine_not_installed')

    async def test_fingerprint_tracks_payload_but_excludes_transient_readiness(self) -> None:
        request = self.request(MusicQueueEngineSelection(engine='ace_step'))
        before = await music_queue_plan.preview(request)
        state = ready_catalog()
        state.engines[0].managed_status = 'running'
        state.engines[0].owned_instance = 'a' * 32
        with patch.object(music_queue_plan, 'catalog', new=AsyncMock(return_value=state)):
            after = await music_queue_plan.preview(request)
        self.assertEqual(before.fingerprint, after.fingerprint)
        changed = await music_queue_plan.preview(self.request(MusicQueueEngineSelection(engine='ace_step'), description='Changed'))
        self.assertNotEqual(before.fingerprint, changed.fingerprint)

    async def test_enqueue_metadata_does_not_change_the_preview_fingerprint(self) -> None:
        request = self.request(MusicQueueEngineSelection(engine='ace_step'))
        before = await music_queue_plan.preview(request)
        enqueue = MusicQueueEnqueueRequest(brief=request.brief, selections=request.selections,
            revision=5, submission_key='first_submission', preview_fingerprint=before.fingerprint)
        after = await music_queue_plan.preview(enqueue)
        self.assertEqual(before.fingerprint, after.fingerprint)

    async def test_adapter_identity_is_validated_and_fingerprinted(self) -> None:
        identity = MusicAdapter(id='a' * 32, engine='yue2', target='ar', name='Style', sha256='b' * 64,
            created_at='2026-10-14T00:00:00Z')
        request = self.request(MusicQueueEngineSelection(engine='yue2', ar_adapter=MusicAdapterSelection(id=identity.id)))
        with patch.object(music_queue_plan.music_lora, 'resolve', return_value=Path('/unused')) as resolve, \
             patch.object(music_queue_plan.music_lora, 'get', return_value=identity):
            before = await music_queue_plan.preview(request)
        resolve.assert_called_once_with(request.selections[0].ar_adapter, engine='yue2', target='ar')
        self.assertEqual(before.items[0].adapters, [identity])
        identity = identity.model_copy(update={'sha256': 'c' * 64})
        with patch.object(music_queue_plan.music_lora, 'resolve', return_value=Path('/unused')), \
             patch.object(music_queue_plan.music_lora, 'get', return_value=identity):
            after = await music_queue_plan.preview(request)
        self.assertNotEqual(before.fingerprint, after.fingerprint)
        with patch.object(music_queue_plan.music_lora, 'resolve', side_effect=MusicLoraError('music_lora_target_mismatch')), \
             patch.object(music_queue_plan.music_lora, 'get', return_value=identity):
            result = await music_queue_plan.preview(request)
        self.assertFalse(result.can_enqueue)
        self.assertEqual(result.models[0].error_code, 'music_lora_target_mismatch')

    async def test_adapter_manifest_change_during_validation_is_not_accepted(self) -> None:
        identity = MusicAdapter(id='a' * 32, engine='yue2', target='ar', name='Style', sha256='b' * 64,
            created_at='2026-10-14T00:00:00Z')
        request = self.request(MusicQueueEngineSelection(engine='yue2', ar_adapter=MusicAdapterSelection(id=identity.id)))
        with patch.object(music_queue_plan.music_lora, 'resolve', return_value=Path('/unused')), \
             patch.object(music_queue_plan.music_lora, 'get', side_effect=[identity, identity.model_copy(update={'name': 'Changed'})]):
            result = await music_queue_plan.preview(request)
        self.assertFalse(result.can_enqueue)
        self.assertEqual(result.models[0].error_code, 'music_lora_adapter_changed')


class MusicQueueReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_ace_environment_is_selectable_while_stopped_without_managed_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment = ModuleEnvironment.for_root(Path(temporary), platform='darwin', architecture='arm64')
            root = environment.paths['ace_step']
            for marker in music_queue_plan.DEFINITIONS['ace_step'].source_markers:
                path = root / marker
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'fixture')
            for name in ('.venv/bin/python', '.venv/bin/acestep-api'):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'fixture')
            (root / '.venv/lib/python3.12/site-packages').mkdir(parents=True)
            self.assertFalse(music_queue_plan.environment_verified(environment, 'ace_step'))
            self.assertTrue(music_queue_plan._native_installed(environment, 'ace_step'))
            (root / '.venv/bin/acestep-api').unlink()
            self.assertFalse(music_queue_plan._native_installed(environment, 'ace_step'))

    async def test_empty_installation_never_loads_models_and_reports_all_engines(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment = ModuleEnvironment.for_root(Path(temporary), platform='darwin', architecture='arm64')
            with patch.object(music_queue_plan.manager, 'switch_to', new=AsyncMock()) as switch:
                result = await music_queue_plan.catalog(environment)
            switch.assert_not_awaited()
        self.assertEqual([item.engine for item in result.engines], ['ace_step', 'yue2', 'stable_audio3'])
        self.assertTrue(all(not item.ready for item in result.engines))
        self.assertTrue(all(not item.installed for item in result.engines))

    async def test_selected_stable_variant_requires_its_own_weights(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment = ModuleEnvironment.for_root(Path(temporary), platform='darwin', architecture='arm64')
            for path in music_queue_plan.local_music_runtime.weight_paths(environment, 'stable_audio3'):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'fixture')
            brief = MusicQueueBrief(description='Piano', instrumental=True, duration_seconds=95)
            with patch.object(music_queue_plan, 'catalog', new=AsyncMock(return_value=ready_catalog())):
                small = await music_queue_plan.preview(MusicQueuePreviewRequest(brief=brief,
                    selections=[MusicQueueEngineSelection(engine='stable_audio3')]), environment)
                medium = await music_queue_plan.preview(MusicQueuePreviewRequest(brief=brief,
                    selections=[MusicQueueEngineSelection(engine='stable_audio3', variant='medium')]), environment)
        self.assertTrue(small.can_enqueue)
        self.assertFalse(medium.can_enqueue)
        self.assertEqual(medium.models[0].readiness.error_code, 'music_queue_weights_missing')

    async def test_selected_yue_precision_requires_all_sidecars_and_matching_weights(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment = ModuleEnvironment.for_root(Path(temporary), platform='darwin', architecture='arm64')
            root = music_queue_plan._yue_root(environment)
            for name in ('yue2-3b-q4_0.gguf', 'yue2-vae-f16.gguf', 'sidecars/yue2-model-config.json',
                         'sidecars/yue2-generation-config.json', 'sidecars/yue2-qwen.tiktoken', 'sidecars/yue2-vae-config.json'):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'fixture')
            self.assertFalse(music_queue_plan._weights_ready(environment, MusicQueueEngineSelection(engine='yue2')))
            self.assertTrue(music_queue_plan._weights_ready(environment, MusicQueueEngineSelection(engine='yue2', precision='q4_0')))
            (root / 'sidecars/yue2-qwen.tiktoken').unlink()
            self.assertFalse(music_queue_plan._weights_ready(environment, MusicQueueEngineSelection(engine='yue2', precision='q4_0')))

    async def test_running_native_status_without_exact_ownership_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment = ModuleEnvironment.for_root(Path(temporary), platform='darwin', architecture='arm64',
                paths={'ace_step': music_queue_plan.config.ACE_STEP_DIR})
            with patch.object(music_queue_plan, '_native_installed', return_value=True), \
                 patch.object(music_queue_plan, '_weights_ready', return_value=True), \
                 patch.object(music_queue_plan.manager.state, 'models', {'ace_step': ModelRuntimeState('ace_step', ModelStatus.RUNNING)}), \
                 patch.object(music_queue_plan.manager, 'owned_instance', return_value=None), \
                 patch.object(music_queue_plan, 'runtime_status', new=AsyncMock()) as probe:
                result = await music_queue_plan._native_readiness(environment, 'ace_step')
            probe.assert_not_awaited()
        self.assertFalse(result.ready)
        self.assertEqual(result.error_code, 'music_queue_engine_not_owned')

    async def test_owned_verified_runtime_is_installation_evidence_without_setup_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment = ModuleEnvironment.for_root(Path(temporary), platform='darwin', architecture='arm64',
                paths={'ace_step': music_queue_plan.config.ACE_STEP_DIR})
            with patch.object(music_queue_plan, '_native_installed', return_value=False), \
                 patch.object(music_queue_plan, '_weights_ready', return_value=True), \
                 patch.object(music_queue_plan.manager.state, 'models', {'ace_step': ModelRuntimeState('ace_step', ModelStatus.RUNNING)}), \
                 patch.object(music_queue_plan.manager, 'owned_instance', return_value='a' * 32), \
                 patch.object(music_queue_plan, 'runtime_status', new=AsyncMock(return_value=RuntimeEvidence(installed=True))):
                result = await music_queue_plan._native_readiness(environment, 'ace_step')
        self.assertTrue(result.ready)
        self.assertTrue(result.installed)


class MusicQueueWeightEvidenceTests(unittest.TestCase):
    def test_ace_index_requires_existing_confined_shards(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            component = root / 'component'
            component.mkdir()
            index = component / 'model.safetensors.index.json'
            index.write_text('{"weight_map":{"layer":"model-1.safetensors"}}')
            self.assertFalse(music_queue_plan._ace_component(component))
            (component / 'model-1.safetensors').write_bytes(b'fixture')
            self.assertTrue(music_queue_plan._ace_component(component))
            (root / 'outside.safetensors').write_bytes(b'fixture')
            index.write_text('{"weight_map":{"layer":"../outside.safetensors"}}')
            self.assertFalse(music_queue_plan._ace_component(component))


if __name__ == '__main__':
    unittest.main()
