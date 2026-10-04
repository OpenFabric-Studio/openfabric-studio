"""Offline engine regressions; transformer/model loading is never exercised."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ENGINE = REPO / 'external' / 'ltx-2-mlx'


class EngineTests(unittest.TestCase):
    def test_render_arguments_use_pinned_local_paths_preserve_prompt_and_keep_audio_with_images(self) -> None:
        from app.video_engine import (EngineReadiness, ImageReference, RenderSettings,
            VideoEngineError, render_argv)
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            audio, image = root / 'audio.wav', root / 'reference.png'
            audio.write_bytes(b'isolated boundary fixture')
            image.write_bytes(b'isolated boundary fixture')
            ready = EngineReadiness('ltx23', True, True, 'f' * 64, 'a' * 40, 'b' * 40,
                1, 0, 1000000000, (), ())
            settings = RenderSettings(root / 'out.mp4', 'd' * 2000 + '\n' + 's' * 2000, 193,
                source_audio=audio, references=(ImageReference(image, 0, .75),),
                stage2_steps=2, temporal_tiles=2, spatial_tiles=2, negative_prompt='avoid blur')
            with patch('app.video_engine.inspect_readiness', return_value=ready), \
                    patch('urllib.request.urlopen', side_effect=AssertionError('Never download while rendering')):
                argv = render_argv(ENGINE, root / 'cache', settings)
            self.assertEqual(argv[argv.index('--prompt') + 1], settings.prompt)
            self.assertIn('a2v', argv)
            self.assertEqual(argv[argv.index('--audio') + 1], str(audio))
            self.assertEqual(argv[argv.index('--image') + 1:argv.index('--image') + 4], [str(image), '0', '0.75'])
            self.assertNotIn('--no-audio', argv)
            self.assertEqual(argv[argv.index('--stage2-steps') + 1], '2')
            model = Path(argv[argv.index('--model') + 1])
            self.assertTrue(model.is_relative_to((root / 'cache').resolve()))
            self.assertEqual(len(model.name), 40)
            with patch('app.video_engine.inspect_readiness', return_value=EngineReadiness(
                    'ltx23', False, True, 'f' * 64, 'a' * 40, 'b' * 40, 1, 1, 1000, ('missing',), ())):
                with self.assertRaises(VideoEngineError) as failure:
                    render_argv(ENGINE, root / 'cache', settings)
            self.assertEqual(failure.exception.code, 'model_not_installed')

    def test_character_adapter_is_passed_only_to_picture_generation(self) -> None:
        from app.video_engine import EngineReadiness, ImageReference, RenderSettings, VideoEngineError, render_argv
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            image = root / 'reference.png'
            image.write_bytes(b'isolated boundary fixture')
            adapter = root / 'adapter.safetensors'
            adapter.write_bytes(b'lora' * 400)
            ready = EngineReadiness('ltx23', True, True, 'f' * 64, 'a' * 40, 'b' * 40, 1, 0, 1000000000, (), ())
            picture = RenderSettings(root / 'out.mp4', 'a person walks', 97, references=(ImageReference(image),), mode='i2v', adapter=adapter)
            song = RenderSettings(root / 'out.mp4', 'a person walks', 97, source_audio=image, mode='a2v', adapter=adapter)
            with patch('app.video_engine.inspect_readiness', return_value=ready):
                argv = render_argv(ENGINE, root / 'cache', picture)
                self.assertEqual(argv[argv.index('--lora') + 1], str(adapter.resolve()))
                self.assertEqual(argv[argv.index('--lora') + 2], '1.0')
                self.assertIn('generate', argv)
                self.assertNotIn('a2v', argv)
                with self.assertRaises(VideoEngineError) as failure:
                    render_argv(ENGINE, root / 'cache', song)
            self.assertEqual(failure.exception.code, 'bad_settings')

    def test_verified_receipt_is_reused_only_for_unchanged_pinned_file_identities(self) -> None:
        from app.video_engine import RequiredArtifact
        import app.video_engine as engine
        self.assertTrue(hasattr(engine, 'verified_cached_fingerprint'), 'Bounded verified receipt check is required')
        with tempfile.TemporaryDirectory() as scratch:
            cache = Path(scratch)
            path = cache / 'model'
            path.write_bytes(b'valid')
            artifact = RequiredArtifact(path, 'test/repo', 'a' * 40, 'model', 5,
                hashlib.sha256(b'valid').hexdigest(), None)
            with patch('app.video_engine.required_artifacts', return_value=(artifact,)):
                self.assertIsNone(engine.verified_cached_fingerprint(cache))
                fingerprint = engine.verify_and_record_artifacts(cache)
                with patch('app.video_engine.verify_artifacts', side_effect=AssertionError('No streaming in receipt checks')):
                    self.assertEqual(engine.verified_cached_fingerprint(cache), fingerprint)
                replacement = cache / 'replacement'
                replacement.write_bytes(b'wrong')
                old = path.stat()
                os.utime(replacement, ns=(old.st_atime_ns, old.st_mtime_ns))
                replacement.replace(path)
                self.assertIsNone(engine.verified_cached_fingerprint(cache))

    def test_malformed_and_escaped_verification_receipts_are_not_trusted(self) -> None:
        from app.video_engine import RequiredArtifact
        import app.video_engine as engine
        self.assertTrue(hasattr(engine, 'verified_cached_fingerprint'))
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            cache = root / 'cache'
            cache.mkdir()
            path = cache / 'model'
            path.write_bytes(b'valid')
            artifact = RequiredArtifact(path, 'test/repo', 'a' * 40, 'model', 5,
                hashlib.sha256(b'valid').hexdigest(), None)
            with patch('app.video_engine.required_artifacts', return_value=(artifact,)):
                engine.verify_and_record_artifacts(cache)
                receipt = cache / 'video-engine/ltx23-provenance.json'
                saved = receipt.read_bytes()
                receipt.write_text('{invalid JSON')
                self.assertIsNone(engine.verified_cached_fingerprint(cache))
                receipt.write_bytes(saved)
                record = json.loads(saved)
                record['engine_commit'] = 'b' * 40
                receipt.write_text(json.dumps(record))
                self.assertIsNone(engine.verified_cached_fingerprint(cache))
                record = json.loads(saved)
                record['schema_version'] = True
                receipt.write_text(json.dumps(record))
                self.assertIsNone(engine.verified_cached_fingerprint(cache), 'JSON booleans are not integer schema versions')
                record = json.loads(saved)
                record['artifacts'][0]['size'] = 5.0
                receipt.write_text(json.dumps(record))
                self.assertIsNone(engine.verified_cached_fingerprint(cache), 'Receipt size must be an integer')
                outside = root / 'outside.json'
                outside.write_bytes(saved)
                receipt.unlink()
                receipt.symlink_to(outside)
                self.assertIsNone(engine.verified_cached_fingerprint(cache))

    @unittest.skipUnless((ENGINE / '.venv/bin/python').is_file(), 'Installed Apple Silicon CPU tiler only')
    def test_patched_stages_reconstruct_tiled_video_keep_full_audio_and_refresh_transformer_on_cpu(self) -> None:
        script = '''
import ast, json, sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path.cwd() / 'backend'))
from app.video_engine import patched_sources
import mlx.core as mx
import numpy as np
mx.set_default_device(mx.cpu)
from ltx_core_mlx.model.transformer.model import X0Model
from ltx_core_mlx.model.video_vae.tiling import TileCountConfig, DimensionTilingConfig
from ltx_core_mlx.utils.positions import compute_video_positions
from ltx_pipelines_mlx.scheduler import STAGE_2_SIGMAS, shorten_schedule
tree = ast.parse(patched_sources(Path('external/ltx-2-mlx'))['ltx_pipelines_mlx.a2vid_two_stage'])
function = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == 'generate_and_save')
selected = [node for node in function.body if
    isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id in ('stage1_dit','x0_model','stage2_model')
    or isinstance(node, ast.If) and ast.unparse(node.test) == 'self._tile_count is not None']
seen = []
class Boundary:
    def __init__(self, stage):
        self.stage = stage
    def __call__(self, **kwargs):
        seen.append((self.stage, kwargs['video_latent'].shape[1], kwargs['audio_latent'].shape[1]))
        return kwargs['video_latent'], kwargs['audio_latent']
tiles = TileCountConfig(frames=DimensionTilingConfig(2,1),height=DimensionTilingConfig(2,1),width=DimensionTilingConfig(2,1))
owner = SimpleNamespace(dit=Boundary(1),_tile_count=tiles)
scope = dict(self=owner,F=5,H_half=4,W_half=4,H_full=8,W_full=8,X0Model=X0Model)
exec(compile(ast.Module(body=selected[:3], type_ignores=[]), '<stage1>', 'exec'), scope)
owner.dit = Boundary(2)
exec(compile(ast.Module(body=selected[3:], type_ignores=[]), '<stage2>', 'exec'), scope)
for stage,shape,name in ((1,(5,4,4),'x0_model'),(2,(5,8,8),'stage2_model')):
    count = int(np.prod(shape))
    video = mx.arange(count * 3).astype(mx.float32).reshape(1,count,3)
    audio = mx.arange(14).astype(mx.float32).reshape(1,7,2)
    video_out,audio_out = scope[name](video_latent=video,audio_latent=audio,sigma=mx.array([.5]),video_positions=compute_video_positions(*shape))
    np.testing.assert_allclose(np.array(video_out), np.array(video)*.5, rtol=1e-5)
    np.testing.assert_allclose(np.array(audio_out), np.array(audio)*.5, rtol=1e-5)
    calls = [entry for entry in seen if entry[0] == stage]
    assert len(calls) == 8 and max(entry[1] for entry in calls) < count
    assert all(entry[2] == 7 for entry in calls)
assert [len(shorten_schedule(STAGE_2_SIGMAS, steps, keep='tail'))-1 for steps in (1,2,3)] == [1,2,3]
print(json.dumps({'calls':len(seen),'device':'cpu','refinement':[1,2,3]}))
'''
        result = subprocess.run([str(ENGINE / '.venv/bin/python'), '-c', script], cwd=REPO,
            env={**os.environ, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'},
            capture_output=True, text=True, timeout=30, check=True)
        self.assertEqual(json.loads(result.stdout.splitlines()[-1]), {'calls': 16, 'device': 'cpu', 'refinement': [1, 2, 3]})

    @unittest.skipUnless((ENGINE / '.venv/bin/python').is_file(), 'Installed Apple Silicon engine only')
    def test_installed_cli_receives_generation_option_matrix_without_loading_models(self) -> None:
        from app.video_engine import EngineReadiness, ImageReference, RenderSettings, render_argv

        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            audio, image = root / 'audio.wav', root / 'reference.png'
            audio.write_bytes(b'mocked audio boundary')
            image.write_bytes(b'mocked image boundary')
            requests: list[list[str]] = []
            expected: list[dict[str, object]] = []
            for profile in ('ltx23', 'ltx25'):
                ready = EngineReadiness(profile, True, True, 'f' * 64, 'a' * 40, 'b' * 40,
                    1, 0, 1000000000, (), ())
                for index, seconds in enumerate((2, 4, 6, 8, 10, 12)):
                    width, height = ((704, 448), (768, 512), (1280, 704))[index % 3]
                    reference = ImageReference(image, 0, (0.0, .7, 1.0)[index % 3])
                    settings = RenderSettings(root / 'out.mp4', f'{profile} scene {seconds}', seconds * 24 + 1,
                        source_audio=audio, references=(reference,) if index % 2 else (), profile_id=profile,
                        width=width, height=height, seed=0 if index % 2 else 2147483647,
                        stage1_steps=(10, 30, 50)[index % 3], stage2_steps=index % 3 + 1,
                        cfg_scale=(1.0, 3.0, 8.0)[index % 3], negative_prompt='avoid blur' if index % 2 else None,
                        temporal_tiles=2 if seconds > 6 else 1, spatial_tiles=2 if width >= 1280 else 1)
                    with patch('app.video_engine.inspect_readiness', return_value=ready):
                        argv = render_argv(ENGINE, root / 'cache', settings)
                    requests.append(argv[argv.index('--') + 1:] + ['--quiet'])
                    expected.append({'prompt': settings.prompt, 'frames': settings.frames,
                        'width': width, 'height': height, 'fps': 24.0, 'seed': settings.seed,
                        'stage1': settings.stage1_steps, 'stage2': settings.stage2_steps,
                        'cfg': settings.cfg_scale, 'negative': settings.negative_prompt,
                        'audio': str(audio), 'references': 1 if settings.references else 0,
                        'strength': reference.strength if settings.references else None,
                        'temporal': settings.temporal_tiles, 'spatial': settings.spatial_tiles,
                        'model': argv[argv.index('--model') + 1], 'gemma': argv[argv.index('--gemma') + 1],
                        'low_ram': True})
            script = '''
import json, sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path.cwd() / 'backend'))
from app.video_engine import install_compatibility
install_compatibility(Path('external/ltx-2-mlx'))
from ltx_pipelines_mlx.cli import _build_parser, _cmd_a2v
from ltx_pipelines_mlx.utils.args import ImageConditioningInput
from ltx_core_mlx.model.video_vae.tiling import TileCountConfig
seen: list[dict[str, object]] = []
class Boundary:
    def __init__(self, *, model_dir: str, gemma_model_id: str, low_ram_streaming: bool,
                 tile_count: TileCountConfig | None) -> None:
        self.record: dict[str, object] = dict(model=model_dir,gemma=gemma_model_id,
            low_ram=low_ram_streaming,temporal=tile_count.frames.num_tiles if tile_count else 1,
            spatial=tile_count.height.num_tiles if tile_count else 1)
    def generate_and_save(self, *, prompt: str, output_path: str, audio_path: str,
                          height: int, width: int, num_frames: int, frame_rate: float, seed: int,
                          images: list[ImageConditioningInput] | None, audio_start_time: float,
                          stage1_steps: int, stage2_steps: int, cfg_scale: float,
                          negative_prompt: str | None = None) -> None:
        references = images or []
        self.record.update(prompt=prompt,frames=num_frames,width=width,
            height=height,fps=frame_rate,seed=seed,stage1=stage1_steps,
            stage2=stage2_steps,cfg=cfg_scale,negative=negative_prompt,
            audio=audio_path,references=len(references),strength=references[0].strength if references else None)
        seen.append(self.record)
with patch('ltx_pipelines_mlx.a2vid_two_stage.A2VidPipelineTwoStage', Boundary):
    for request in REQUESTS:
        _cmd_a2v(_build_parser().parse_args(request))
print(json.dumps(seen))
'''.replace('REQUESTS', repr(requests))
            result = subprocess.run([str(ENGINE / '.venv/bin/python'), '-c', script], cwd=REPO,
                env={**os.environ, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'},
                capture_output=True, text=True, timeout=30, check=True)
            self.assertEqual(json.loads(result.stdout.splitlines()[-1]), expected)

    @unittest.skipUnless((ENGINE / '.venv/bin/python').is_file(), 'Installed Apple Silicon engine only')
    def test_runtime_wrapper_passes_a2v_tiles_to_constructor_in_real_installed_cli(self) -> None:
        runner = REPO / 'backend/scripts/run_video.py'
        self.assertTrue(runner.is_file(), 'A tracked offline entry point is required')
        script = '''
import sys, json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path.cwd() / 'backend'))
from app.video_engine import install_compatibility
install_compatibility(Path('external/ltx-2-mlx'))
from ltx_pipelines_mlx.cli import _build_parser, _cmd_a2v
seen = {}
class Boundary:
    def __init__(self, **kwargs):
        tiles = kwargs['tile_count']
        seen.update(temporal=tiles.frames.num_tiles, spatial=tiles.height.num_tiles)
    def generate_and_save(self, **kwargs):
        seen['frames'] = kwargs['num_frames']
args = _build_parser().parse_args(['a2v','-p','test','-o','unused.mp4','--audio','unused.wav','--frame-rate','24','--tile-frames','2','--tile-spatial','2'])
with patch('ltx_pipelines_mlx.a2vid_two_stage.A2VidPipelineTwoStage', Boundary):
    _cmd_a2v(args)
print(json.dumps(seen))
'''
        result = subprocess.run([str(ENGINE / '.venv/bin/python'), '-c', script], cwd=REPO,
            env={**os.environ, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'},
            capture_output=True, text=True, timeout=30, check=True)
        self.assertEqual(json.loads(result.stdout.splitlines()[-1]), {'temporal': 2, 'spatial': 2, 'frames': 97})

    def test_tracked_wrapper_exists_instead_of_modifying_vendor_checkout(self) -> None:
        self.assertIsNotNone(importlib.util.find_spec('app.video_engine'), 'A tracked offline video wrapper is required')

    @unittest.skipUnless((ENGINE / 'pyproject.toml').is_file(), 'Optional inspected vendor checkout')
    def test_a2v_patch_connects_cli_and_both_denoising_stages_without_file_writes(self) -> None:
        from app.video_engine import patched_sources
        relative = 'packages/ltx-pipelines-mlx/src/ltx_pipelines_mlx/a2vid_two_stage.py'
        original = (ENGINE / relative).read_bytes()
        sources = patched_sources(ENGINE)
        cli = ast.parse(sources['ltx_pipelines_mlx.cli'])
        handler = next(node for node in cli.body if isinstance(node, ast.FunctionDef) and node.name == '_cmd_a2v')
        constructor = next(node for node in ast.walk(handler) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'PipeClass')
        self.assertIn('tile_count', [item.arg for item in constructor.keywords])
        pipeline = ast.parse(sources['ltx_pipelines_mlx.a2vid_two_stage'])
        tiled_calls = [node for node in ast.walk(pipeline) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'VideoModalityTiler']
        self.assertEqual(len(tiled_calls), 2, 'Half-resolution and full-resolution denoising need separate tilers')
        self.assertEqual((ENGINE / relative).read_bytes(), original)
        self.assertEqual(patched_sources(ENGINE), sources)

    def test_unknown_or_symlinked_vendor_sources_fail_closed(self) -> None:
        from app.video_engine import VideoEngineError, patched_sources
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            with self.assertRaises(VideoEngineError) as failure:
                patched_sources(root)
            self.assertEqual(failure.exception.code, 'engine_incompatible')

    def test_refinement_above_three_and_long_unbounded_prompts_are_rejected(self) -> None:
        import app.video_engine as engine
        self.assertTrue(hasattr(engine, 'RenderSettings'), 'Bounded render settings are required')
        from app.video_engine import RenderSettings, VideoEngineError, validate_settings
        for settings in [RenderSettings(Path('out.mp4'), 'scene', 97, stage2_steps=4),
                         RenderSettings(Path('out.mp4'), 'x' * 4002, 97)]:
            with self.assertRaises(VideoEngineError):
                validate_settings(settings)
        validate_settings(RenderSettings(Path('out.mp4'), 'x' * 4001, 97))

    def test_preflight_reports_exact_missing_bytes_without_network_or_creating_cache(self) -> None:
        import app.video_engine as engine
        self.assertTrue(hasattr(engine, 'inspect_readiness'), 'Offline readiness is required')
        from app.video_engine import inspect_readiness, required_artifacts
        with tempfile.TemporaryDirectory() as scratch:
            cache = Path(scratch) / 'not-created'
            with patch('urllib.request.urlopen', side_effect=AssertionError('No network in readiness')):
                artifacts = required_artifacts(cache, 'ltx23')
                readiness = inspect_readiness(ENGINE, cache)
            self.assertFalse(readiness.ready)
            self.assertEqual(readiness.engine_ready, (ENGINE / '.venv/bin/python').is_file())
            self.assertEqual(readiness.uncached_bytes, sum(item.size for item in artifacts))
            self.assertEqual(readiness.expected_bytes, readiness.uncached_bytes)
            self.assertFalse(cache.exists())
            names = [item.relative_path for item in artifacts]
            self.assertNotIn('transformer-distilled.safetensors', names)
            self.assertNotIn('ltx-2.3-22b-distilled-lora-384.safetensors', names)
            self.assertIn('transformer-distilled-1.1.safetensors', names)

    def test_content_verification_rejects_same_size_corruption_and_escaped_symlink(self) -> None:
        from app.video_engine import RequiredArtifact, VideoEngineError
        import app.video_engine as engine
        self.assertTrue(hasattr(engine, 'verify_artifacts'), 'Explicit content verification is required')
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            cache = root / 'cache'
            cache.mkdir()
            path = cache / 'model'
            path.write_bytes(b'valid')
            artifact = RequiredArtifact(path, 'test/repo', 'a' * 40, 'model', 5,
                hashlib.sha256(b'valid').hexdigest(), None)
            with patch('app.video_engine.required_artifacts', return_value=(artifact,)):
                fingerprint = engine.verify_artifacts(cache)
                self.assertEqual(len(fingerprint), 64)
                path.write_bytes(b'wrong')
                with self.assertRaises(VideoEngineError) as failed:
                    engine.verify_artifacts(cache)
                self.assertEqual(failed.exception.code, 'model_integrity_failed')
                path.unlink()
                outside = root / 'outside'
                outside.write_bytes(b'valid')
                path.symlink_to(outside)
                with self.assertRaises(VideoEngineError):
                    engine.verify_artifacts(cache)

    def test_readiness_counts_existing_hub_blob_as_cached_but_not_installed(self) -> None:
        from app.video_engine import RequiredArtifact, inspect_readiness
        with tempfile.TemporaryDirectory() as scratch:
            cache = Path(scratch)
            digest = hashlib.sha256(b'valid').hexdigest()
            pack = cache / 'hub/models--test--repo'
            path = pack / 'snapshots' / ('a' * 40) / 'model'
            artifact = RequiredArtifact(path, 'test/repo', 'a' * 40, 'model', 5, digest, None)
            blob = pack / 'blobs' / digest
            blob.parent.mkdir(parents=True)
            blob.write_bytes(b'valid')
            with patch('app.video_engine.required_artifacts', return_value=(artifact,)):
                ready = inspect_readiness(ENGINE, cache)
            self.assertEqual(ready.uncached_bytes, 0)
            self.assertFalse(ready.ready)
            self.assertEqual(ready.missing_files, ('test/repo/model',))

    def test_patch_behaviour_runs_without_vendor_install_using_reviewed_signatures(self) -> None:
        from app.video_engine import patched_sources
        # Small source fixtures retain exactly the reviewed substitutions; no MLX import.
        cli = '''def _cmd_a2v(args):
    pipe = PipeClass(
        low_ram_streaming=getattr(args, "low_ram", False),
    )
\ndef _cmd_retake(args):
    pass
'''
        a2v = '''class Pipeline:
    def generate(self):
        x0_model = X0Model(self.dit)
        output_2 = denoise_loop(
            model=x0_model,
        )
'''
        prefix = 'packages/ltx-pipelines-mlx/src/ltx_pipelines_mlx/'
        fixtures = {prefix + 'cli.py': cli, prefix + 'a2vid_two_stage.py': a2v,
            prefix + 'ti2vid_two_stages.py': '# unchanged base dependency\n',
            'packages/ltx-core-mlx/src/ltx_core_mlx/components/modality_tiling.py': '# unchanged tiler dependency\n'}
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            for relative, source in fixtures.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source)
            hashes = {relative: hashlib.sha256(source.encode()).hexdigest() for relative, source in fixtures.items()}
            with patch('app.video_engine.SOURCE_HASHES', hashes):
                sources = patched_sources(root)
                calls: list[dict[str, object]] = []
                scope: dict[str, object] = {'PipeClass': lambda **kwargs: calls.append(kwargs),
                    '_build_tile_count_config': lambda args: 'requested-tiles'}
                exec(sources['ltx_pipelines_mlx.cli'], scope)
                exec('_cmd_a2v(object())', scope)
                self.assertEqual(calls[0]['tile_count'], 'requested-tiles')
                tree = ast.parse(sources['ltx_pipelines_mlx.a2vid_two_stage'])
                tilers = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name) and node.func.id == 'VideoModalityTiler']
                self.assertEqual([ast.unparse(next(arg.value for arg in node.keywords if arg.arg == 'latent_shape'))
                    for node in tilers], ['(F, H_half, W_half)', '(F, H_full, W_full)'])
                (root / (prefix + 'cli.py')).write_text(cli + '# local edit\n')
                from app.video_engine import VideoEngineError
                with self.assertRaises(VideoEngineError):
                    patched_sources(root)
