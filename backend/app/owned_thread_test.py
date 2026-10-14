from __future__ import annotations

import asyncio
import threading
import unittest

from app import job_lifecycle


class OwnedThreadTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancellation_waits_until_filesystem_worker_exits(self) -> None:
        worker = getattr(job_lifecycle, 'run_owned_thread', None)
        self.assertIsNotNone(worker, 'File inspection threads must be drained before cleanup')
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        def inspect() -> int:
            entered.set()
            release.wait(2)
            finished.set()
            return 1
        task = asyncio.create_task(worker(inspect))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
            self.assertTrue(finished.is_set())
        finally:
            release.set()


if __name__ == '__main__':
    unittest.main()
