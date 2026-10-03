from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from app.module_catalog import ModuleEnvironment, engine_python
from app.module_evidence import capability_verified, environment_fingerprint, record_capability_success


class ModuleEvidenceTests(unittest.TestCase):
    def test_receipt_is_bound_to_packages_checkout_and_service(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment = ModuleEnvironment.for_root(Path(temporary), platform='linux', architecture='x64')
            engine = environment.paths['speech']
            (engine / 'GPT_SoVITS').mkdir(parents=True)
            (engine / 'api.py').write_text('# source')
            python = engine_python(engine, environment.platform)
            python.parent.mkdir(parents=True)
            python.write_text('interpreter')
            site = engine / '.venv/lib/python3.11/site-packages'
            (site / 'torch-2.11.dist-info').mkdir(parents=True)
            (site / 'torch').mkdir()
            before = environment_fingerprint(environment, 'speech')
            record_capability_success('speech', environment=environment, service_identity='http://127.0.0.1:9880')
            self.assertTrue(capability_verified(environment, 'speech', 'http://127.0.0.1:9880'))
            self.assertFalse(capability_verified(environment, 'speech', 'http://127.0.0.1:9999'))
            (site / 'torch').rmdir()
            self.assertNotEqual(before, environment_fingerprint(environment, 'speech'))
            self.assertFalse(capability_verified(environment, 'speech', 'http://127.0.0.1:9880'))

    def test_status_fingerprinting_does_not_create_missing_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment = ModuleEnvironment.for_root(root)
            self.assertIsNone(environment_fingerprint(environment, 'speech'))
            self.assertFalse(capability_verified(environment, 'speech', 'http://127.0.0.1:9880'))
            self.assertEqual(list(root.iterdir()), [])
