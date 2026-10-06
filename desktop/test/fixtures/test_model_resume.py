"""Offline regression harness for the patched, pinned Apache-2.0 upstream tool."""
from __future__ import annotations

import argparse
import errno
import hashlib
import importlib.util
import io
import json
from contextlib import redirect_stdout
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request
from dataclasses import replace

source_root = Path(sys.argv.pop(1))
spec = importlib.util.spec_from_file_location("model_manager", source_root / "tools/model_manager_v2.py")
assert spec is not None and spec.loader is not None
manager = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = manager
spec.loader.exec_module(manager)


class Response(io.BytesIO):
    def __init__(self, body: bytes, status: int = 200, headers: dict[str, str] | None = None, drop: bool = False) -> None:
        super().__init__(body)
        self.status = status
        self.headers = headers or {"Content-Length": str(len(body))}
        self.drop = drop
        self.read_count = 0

    def read(self, size: int = -1) -> bytes:
        self.read_count += 1
        if self.drop and self.read_count > 1:
            raise OSError("simulated connection loss")
        return super().read(size)


class ModelResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.output = self.root / "model.bin"
        self.body = b"complete model bytes"
        self.info = manager.RemoteFileInfo(len(self.body), "a" * 40, hashlib.sha256(self.body).hexdigest())
        self.package = manager.PackageRecord("test", "test_pkg", "test", "test/model", "gguf", "q8", ("model.bin",), "", {"repo": "test/repo", "kind": "huggingface_snapshot", "revision": "main"}, True)
        self.requests: list[object] = []
        self.metadata = patch.object(manager, "check_remote_file", side_effect=lambda *_: self.info)
        self.metadata.start()
        self.addCleanup(self.metadata.stop)
        token = patch.object(manager, "huggingface_token", return_value=None)
        token.start()
        self.addCleanup(token.stop)

    def interrupted(self) -> None:
        with patch.object(manager, "DOWNLOAD_ATTEMPTS", 1, create=True), patch.object(manager, "urlopen", return_value=Response(self.body[:5], headers={"Content-Length": str(len(self.body))}, drop=True)):
            with self.assertRaises((OSError, manager.ManagerError)):
                manager.download_file(self.package, "model.bin", self.output)
        self.assertEqual(self.output.read_bytes(), self.body[:5])

    def test_transient_open_failure_retries_boundedly_without_refreshing_identity(self) -> None:
        responses = [URLError("connection reset"), HTTPError("https://test.invalid", 503, "unavailable", {}, None), Response(self.body)]
        with patch.object(manager, "urlopen", side_effect=responses) as download, patch.object(manager.time, "sleep"):
            manager.download_file(self.package, "model.bin", self.output)
        self.assertEqual(download.call_count, 3)
        self.assertEqual(self.output.read_bytes(), self.body)
        self.assertEqual(self.metadata.target.check_remote_file.call_count, 1)

    def test_short_body_retries_with_the_same_pinned_range(self) -> None:
        first = Response(self.body[:5], headers={"Content-Length": str(len(self.body))})
        def resume(request: Request, **_kwargs: object) -> Response:
            self.assertEqual(request.get_header("Range"), "bytes=5-")
            self.assertIn("a" * 40, request.full_url)
            return Response(self.body[5:], 206, {"Content-Length": str(len(self.body)-5), "Content-Range": f"bytes 5-{len(self.body)-1}/{len(self.body)}"})
        with patch.object(manager, "urlopen") as download, patch.object(manager.time, "sleep"):
            download.side_effect = lambda request, **kwargs: first if download.call_count == 1 else resume(request, **kwargs)
            manager.download_file(self.package, "model.bin", self.output)
        self.assertEqual(download.call_count, 2)
        self.assertEqual(self.output.read_bytes(), self.body)

    def test_exhausted_retries_preserve_partial_and_permanent_failure_never_retries(self) -> None:
        with patch.object(manager, "urlopen", side_effect=URLError("offline")) as download, patch.object(manager.time, "sleep"):
            with self.assertRaises(manager.ManagerError):
                manager.download_file(self.package, "model.bin", self.output)
        self.assertEqual(download.call_count, 3)
        with patch.object(manager, "urlopen", side_effect=HTTPError("https://test.invalid", 403, "forbidden", {}, None)) as download:
            with self.assertRaises(manager.ManagerError):
                manager.download_file(self.package, "model.bin", self.output)
        self.assertEqual(download.call_count, 1)

    def test_stall_timeout_and_backoff_cancellation_are_bounded(self) -> None:
        with patch.object(manager, "urlopen", side_effect=TimeoutError("stalled socket")) as download, patch.object(manager.time, "sleep"):
            with self.assertRaises(manager.ManagerError):
                manager.download_file(self.package, "model.bin", self.output)
        self.assertEqual(download.call_count, 3)
        self.assertTrue(all(call.kwargs["timeout"] == 45 for call in download.call_args_list))
        cancelled = self.root / "cancel"
        with patch.object(manager, "urlopen", return_value=Response(self.body[:5], headers={"Content-Length": str(len(self.body))})) as download, patch.object(manager.time, "sleep", side_effect=lambda _: cancelled.touch()):
            with self.assertRaises(manager.InstallCancelled):
                manager.download_file(self.package, "model.bin", self.output, cancel_file=cancelled)
        self.assertEqual(download.call_count, 1)
        self.assertEqual(self.output.read_bytes(), self.body[:5])

    def test_progress_reports_actual_bytes_attempts_and_digest_phase(self) -> None:
        output = io.StringIO()
        responses = [URLError("offline"), Response(self.body)]
        with redirect_stdout(output), patch.object(manager, "urlopen", side_effect=responses), patch.object(manager.time, "sleep"):
            manager.download_file(self.package, "model.bin", self.output, progress=lambda _received, _total: None)
        events = [json.loads(line.removeprefix("OPENFABRIC_DOWNLOAD_PROGRESS ")) for line in output.getvalue().splitlines()]
        self.assertEqual([event["phase"] for event in events], ["retrying", "downloading", "verifying", "complete"])
        self.assertEqual(events[0]["bytes_received"], 0)
        self.assertEqual(events[-1]["bytes_received"], len(self.body))
        self.assertEqual(events[-1]["attempt"], 2)

    def test_interruption_resumes_pinned_identity(self) -> None:
        self.interrupted()
        def resume(request: object, **_kwargs: object) -> Response:
            self.assertEqual(request.get_header("Range"), "bytes=5-")
            self.assertIn("a" * 40, request.full_url)
            return Response(self.body[5:], 206, {"Content-Length": str(len(self.body) - 5), "Content-Range": f"bytes 5-{len(self.body)-1}/{len(self.body)}"})
        with patch.object(manager, "urlopen", side_effect=resume):
            manager.download_file(self.package, "model.bin", self.output)
        self.assertEqual(self.output.read_bytes(), self.body)

    def test_changed_content_identity_restarts_without_appending(self) -> None:
        self.interrupted()
        self.body = b"different model data"
        self.info = manager.RemoteFileInfo(len(self.body), "b" * 40, hashlib.sha256(self.body).hexdigest())
        def restart(request: object, **_kwargs: object) -> Response:
            self.assertIsNone(request.get_header("Range"))
            return Response(self.body)
        with patch.object(manager, "urlopen", side_effect=restart):
            manager.download_file(self.package, "model.bin", self.output)
        self.assertEqual(self.output.read_bytes(), self.body)

    def test_equal_length_corruption_is_rejected(self) -> None:
        with patch.object(manager, "urlopen", return_value=Response(b"X" * len(self.body))):
            with self.assertRaises(manager.ManagerError):
                manager.download_file(self.package, "model.bin", self.output)

    def test_invalid_range_rejected_without_changing_partial(self) -> None:
        self.interrupted()
        for value in [f"bytes 5-999/{len(self.body)}", f"bytes 4-{len(self.body)-1}/{len(self.body)}", "bytes 5-8/*"]:
            with patch.object(manager, "urlopen", return_value=Response(self.body[5:], 206, {"Content-Length": str(len(self.body)-5), "Content-Range": value})):
                with self.assertRaises(manager.ManagerError):
                    manager.download_file(self.package, "model.bin", self.output)
            self.assertEqual(self.output.read_bytes(), self.body[:5])

    def test_416_does_not_accept_length_without_a_digest(self) -> None:
        self.interrupted()
        error = HTTPError("http://example.invalid", 416, "range", {"Content-Range": "bytes */5"}, io.BytesIO())
        self.addCleanup(error.close)
        with patch.object(manager, "urlopen", side_effect=error):
            with self.assertRaises(manager.ManagerError):
                manager.download_file(self.package, "model.bin", self.output)
        self.assertEqual(self.output.read_bytes(), self.body[:5])

    def test_install_failure_keeps_cleanup_compatible_partial_directory(self) -> None:
        args = argparse.Namespace(models_root=str(self.root), overwrite=False, dry_run=False, check=False, progress=False, cancel_file="")
        with patch.object(manager, "DOWNLOAD_ATTEMPTS", 1, create=True), patch.object(manager, "urlopen", return_value=Response(self.body[:5], headers={"Content-Length": str(len(self.body))}, drop=True)):
            with self.assertRaises((OSError, manager.ManagerError)):
                manager.install_package(self.package, [self.package], args)
        candidates = list(self.root.glob(".test_model.*"))
        self.assertEqual(len([p for p in candidates if p.is_dir()]), 1)
        self.assertEqual(next(p for p in candidates if p.is_dir()).joinpath("model.bin").read_bytes(), self.body[:5])
        manager.clean_partial_package(self.package, args)
        self.assertFalse(any(p.is_dir() for p in self.root.glob(".test_model.*")))

    def test_interrupted_package_promotion_verifies_and_recovers_completed_files(self) -> None:
        package = replace(self.package, files=("model.bin", "sidecar.bin"))
        args = argparse.Namespace(models_root=str(self.root), overwrite=False, dry_run=False, check=False, progress=False, cancel_file="")
        original_replace = Path.replace
        def interrupted(path: Path, target: Path) -> Path:
            if path.name == "sidecar.bin":
                raise OSError("interrupted promotion")
            return original_replace(path, target)
        with patch.object(manager, "urlopen", side_effect=lambda *_args, **_kwargs: Response(self.body)):
            with patch.object(Path, "replace", interrupted):
                with self.assertRaises(OSError):
                    manager.install_package(package, [package], args)
            self.assertEqual((self.root / "test/model/model.bin").read_bytes(), self.body)
            manager.install_package(package, [package], args)
        self.assertEqual((self.root / "test/model/sidecar.bin").read_bytes(), self.body)
        self.assertTrue(manager.package_manifest_path(package, self.root).is_file())
        self.assertFalse(any(p.is_dir() for p in self.root.glob(".test_model.*")))

    def test_missing_digest_refuses_unverifiable_model_content(self) -> None:
        self.info = manager.RemoteFileInfo(len(self.body), "a" * 40, "")
        with patch.object(manager, "urlopen") as download:
            with self.assertRaises(manager.ManagerError):
                manager.download_file(self.package, "model.bin", self.output)
        download.assert_not_called()

    def test_cancellation_retains_partial_download(self) -> None:
        cancel_file = self.root / "cancel"
        class CancelledResponse(Response):
            def read(response, size: int = -1) -> bytes:
                result = super().read(size)
                cancel_file.touch()
                return result
        with patch.object(manager, "urlopen", return_value=CancelledResponse(self.body[:5], headers={"Content-Length": str(len(self.body))})):
            with self.assertRaises(manager.InstallCancelled):
                manager.download_file(self.package, "model.bin", self.output, cancel_file=cancel_file)
        self.assertEqual(self.output.read_bytes(), self.body[:5])

    def test_duplicate_download_writers_are_serialized(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        active = 0
        maximum = 0
        guard = threading.Lock()
        errors: list[BaseException] = []
        def response(_request: object, **_kwargs: object) -> Response:
            nonlocal active, maximum
            with guard:
                active += 1
                maximum = max(maximum, active)
            entered.set()
            release.wait(2)
            with guard:
                active -= 1
            return Response(self.body)
        def worker() -> None:
            try:
                manager.download_file(self.package, "model.bin", self.output)
            except BaseException as error:
                errors.append(error)
        with patch.object(manager, "urlopen", side_effect=response):
            first = threading.Thread(target=worker)
            second = threading.Thread(target=worker)
            first.start()
            self.assertTrue(entered.wait(2))
            second.start()
            time.sleep(0.1)
            release.set()
            first.join(3)
            second.join(3)
        self.assertEqual(errors, [])
        self.assertEqual(maximum, 1)
        self.assertEqual(self.output.read_bytes(), self.body)

    @unittest.skipIf(os.name == "nt", "POSIX lock error path")
    def test_unrecoverable_lock_errors_do_not_wait_forever(self) -> None:
        cancel = self.root / "cancel"
        timer = threading.Timer(0.15, cancel.touch)
        timer.start()
        try:
            with patch("fcntl.flock", side_effect=OSError(errno.EBADF, "invalid lock descriptor")):
                with self.assertRaises((OSError, manager.InstallCancelled)) as caught:
                    with manager.writer_lock(self.root / "lock", cancel):
                        self.fail("invalid lock was accepted")
                self.assertIsInstance(caught.exception, OSError)
        finally:
            timer.cancel()


if __name__ == "__main__":
    unittest.main()
