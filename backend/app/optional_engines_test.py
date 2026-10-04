from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import config
from app.optional_engines import (
    OptionalEngineError, install_command, prepare_chatterbox, prepare_kokoro,
    prepare_rvc, prepare_wan, require_installed,
)


class OptionalEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_install_commands_do_not_fetch_weights_or_forbidden_variants(self) -> None:
        for identifier in ('kokoro', 'chatterbox', 'wan22', 'rvc'):
            command = ' '.join(install_command('uv', identifier, Path('/venv/bin/python'), Path('/engine')))
            lowered = command.lower()
            self.assertNotIn('huggingface', lowered)
            self.assertNotIn('from_pretrained', lowered)
            self.assertNotIn('wan2.2-ti2v', lowered)
            self.assertNotIn('turbo', lowered)
            self.assertNotIn('14b', lowered)
        kokoro = install_command('uv', 'kokoro', Path('/venv/bin/python'), Path('/engine'))
        self.assertIn('-e', kokoro)
        self.assertIn('soundfile', kokoro)
        rvc = install_command('uv', 'rvc', Path('/venv/bin/python'), Path('/engine'))
        self.assertIn('faiss-cpu', rvc)
        self.assertNotIn('-e', rvc)

    def test_missing_checkout_names_the_setup_script(self) -> None:
        with patch.object(config, 'KOKORO_DIR', self.root / 'kokoro'):
            with self.assertRaises(OptionalEngineError) as caught:
                require_installed('kokoro')
        self.assertEqual(caught.exception.code, 'engine_not_installed')
        self.assertIn('./setup_kokoro.sh', caught.exception.detail)

    def test_installed_checkout_still_cannot_generate(self) -> None:
        engine = self.root / 'kokoro'
        (engine / 'kokoro').mkdir(parents=True)
        (engine / 'pyproject.toml').write_text('[project]\nname="kokoro"\n', encoding='utf-8')
        (engine / 'kokoro/pipeline.py').write_text('class KPipeline: pass\n', encoding='utf-8')
        python = engine / '.venv/bin/python'
        python.parent.mkdir(parents=True)
        python.write_text('', encoding='utf-8')
        with patch.object(config, 'KOKORO_DIR', engine):
            self.assertEqual(require_installed('kokoro'), engine)
            with self.assertRaises(OptionalEngineError) as caught:
                prepare_kokoro()
        self.assertEqual(caught.exception.code, 'generation_not_wired')
        self.assertIn('does not clone a person', caught.exception.detail)

    def test_turbo_and_non_5b_wan_are_refused_before_generation(self) -> None:
        with self.assertRaises(OptionalEngineError) as turbo:
            prepare_chatterbox('turbo')
        self.assertEqual(turbo.exception.code, 'chatterbox_turbo_refused')
        with self.assertRaises(OptionalEngineError) as wan:
            prepare_wan('t2v-14b')
        self.assertEqual(wan.exception.code, 'wan_variant_refused')
        with self.assertRaises(OptionalEngineError) as animate:
            prepare_wan('animate')
        self.assertEqual(animate.exception.code, 'wan_variant_refused')
        with patch.object(config, 'RVC_DIR', self.root / 'missing-rvc'):
            with self.assertRaises(OptionalEngineError) as rvc:
                prepare_rvc()
        self.assertEqual(rvc.exception.code, 'engine_not_installed')
        self.assertIn('./setup_rvc.sh', rvc.exception.detail)
