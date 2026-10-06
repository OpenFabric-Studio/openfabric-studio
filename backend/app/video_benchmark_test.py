"""Video benchmark inventory is read-only and inference requires exclusive admission."""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class VideoBenchmarkTests(unittest.IsolatedAsyncioTestCase):
    def test_inventory_never_runs_models_and_missing_weights_are_reported(self) -> None:
        from app.video_benchmark import inventory
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            report=inventory(root/'engine',root/'cache','ltx23')
            self.assertFalse(report.inference_executed)
            self.assertFalse(report.readiness.ready)
            self.assertGreater(report.readiness.uncached_bytes,0)
            self.assertEqual(report.inference_skip_reason,'inventory_only')

    def test_live_app_prevents_an_isolated_gpu_launch_even_with_idle_status(self) -> None:
        from app.video_benchmark import require_exclusive
        with patch('app.video_benchmark.app_running',return_value=True):
            with self.assertRaisesRegex(ValueError,'running_app_present'):
                require_exclusive(True,9000)
        with patch('app.video_benchmark.app_running',return_value=False):
            with self.assertRaisesRegex(ValueError,'exclusive_session_required'):
                require_exclusive(False,9000)

    async def test_failed_output_validation_is_a_persistable_report_and_children_are_drained(self) -> None:
        from app.video_benchmark import inventory, run_case
        from app.video_media import VideoMediaError
        import sys
        from unittest.mock import AsyncMock
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            report=inventory(root/'engine',root/'cache','ltx23')
            from dataclasses import replace
            report.readiness=replace(report.readiness,ready=True)
            with patch('app.video_benchmark.app_running',return_value=False), patch('app.video_benchmark.render_argv',return_value=[sys.executable,'-c','print("CPU fixture")','--']), patch('app.video_media.validate_media',new=AsyncMock(side_effect=VideoMediaError())):
                result=await run_case(report,root/'engine',root/'cache',root/'output',exclusive=True,app_port=9000,width=704,height=448)
            self.assertTrue(result.inference_executed)
            self.assertFalse(result.output_validated)
            self.assertEqual(result.error_code,'benchmark_output_invalid')
            self.assertGreater(result.elapsed_sec or 0,0)
            import json
            self.assertEqual(json.loads((root/'output'/'worker.json').read_text())['returncode'],0)

    def test_windows_rss_is_explicitly_unavailable_without_importing_posix_resource(self) -> None:
        from app.video_benchmark import peak_child_rss
        with patch('app.video_benchmark.sys.platform','win32'), patch.dict('sys.modules',{'resource': None}):
            self.assertIsNone(peak_child_rss())

    def test_mlx_telemetry_rejects_bool_negative_and_malformed_values(self) -> None:
        from app.video_benchmark import mlx_telemetry
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'telemetry.json'
            for payload in ('{', '{"peak_mlx_memory_bytes": true}', '{"peak_mlx_memory_bytes": -1}'):
                path.write_text(payload)
                self.assertIsNone(mlx_telemetry(path))
            path.write_text('{"peak_mlx_memory_bytes": 12345}')
            self.assertEqual(mlx_telemetry(path), 12345)

    async def test_cancel_drains_owned_worker_and_does_not_publish_output(self) -> None:
        import asyncio
        import sys
        from collections.abc import Mapping
        from dataclasses import replace
        from app.video_benchmark import inventory, run_case
        from app.video_process import spawn_owned
        children: list[asyncio.subprocess.Process] = []
        async def capture(argv: list[str], *, receipt_path: Path, stdout: int, env: Mapping[str, str]) -> asyncio.subprocess.Process:
            child = await spawn_owned(argv, receipt_path=receipt_path, stdout=stdout, env=env)
            children.append(child)
            return child
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = inventory(root / 'engine', root / 'cache', 'ltx23')
            report.readiness = replace(report.readiness, ready=True)
            with patch('app.video_benchmark.spawn_owned', new=capture), patch('app.video_benchmark.app_running', return_value=False), patch('app.video_benchmark.render_argv',
                    return_value=[sys.executable, '-c', 'import time; time.sleep(60)', '--']):
                task = asyncio.create_task(run_case(report, root / 'engine', root / 'cache', root / 'output',
                    exclusive=True, app_port=9000, width=704, height=448))
                receipt = root / 'output/worker.json'
                for _ in range(100):
                    if receipt.exists() and children:
                        break
                    await asyncio.sleep(.01)
                self.assertTrue(receipt.exists())
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertIsNotNone(children[0].returncode)
            self.assertFalse((root / 'output/baseline.mp4').exists())

    async def test_dangling_output_symlinks_refuse_spawn_and_preserve_engine(self) -> None:
        from dataclasses import replace
        from app.video_benchmark import inventory, run_case
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            engine, output = root / 'engine', root / 'output'
            engine.mkdir();output.mkdir()
            report = inventory(engine, root / 'cache', 'ltx23')
            report.readiness = replace(report.readiness, ready=True)
            for name in ('baseline.mp4', 'worker.log', 'worker.json', 'telemetry.json'):
                link = output / name
                link.symlink_to(engine / 'unexpected')
                with patch('app.video_benchmark.app_running', return_value=False), patch('app.video_benchmark.spawn_owned') as spawn:
                    with self.assertRaisesRegex(ValueError, 'benchmark_output_exists'):
                        await run_case(report, engine, root / 'cache', output, exclusive=True, app_port=9000, width=704, height=448)
                    spawn.assert_not_called()
                self.assertFalse((engine / 'unexpected').exists())
                link.unlink()
