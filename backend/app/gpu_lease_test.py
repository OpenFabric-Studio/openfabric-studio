"""GPU labels represent the actual lock holder, including cancellation."""
from __future__ import annotations

import asyncio
import unittest

from app.gpu_lease import gpu_lease, gpu_owner


class GpuLeaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_waiter_never_relabels_holder_and_cancel_releases_owner(self) -> None:
        lock = asyncio.Lock()
        entered = asyncio.Event()
        async def train() -> None:
            async with gpu_lease(lock, 'voice_training', 'Singer'):
                entered.set()
                await asyncio.Event().wait()
        async def convert() -> None:
            async with gpu_lease(lock, 'voice_conversion', 'Track'):
                self.assertEqual(gpu_owner(lock).label, 'Track')
        training = asyncio.create_task(train())
        await entered.wait()
        conversion = asyncio.create_task(convert())
        await asyncio.sleep(0)
        self.assertEqual(gpu_owner(lock).reason, 'voice_training')
        conversion.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await conversion
        self.assertEqual(gpu_owner(lock).label, 'Singer')
        training.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await training
        self.assertFalse(lock.locked())
        self.assertEqual(gpu_owner(lock).reason, 'gpu_busy')

    async def test_unannotated_holder_has_honest_fallback(self) -> None:
        lock = asyncio.Lock()
        async with lock:
            self.assertEqual(gpu_owner(lock).reason, 'gpu_busy')
            self.assertEqual(gpu_owner(lock).label, '')


if __name__ == '__main__':
    unittest.main()
