from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.managed_layout import engine_default, tool_default


class ManagedLayoutTests(unittest.TestCase):
    def test_selected_root_uses_writable_engine_and_tool_subdirectories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {'OPENFABRIC_MODULE_ROOT': temporary}):
            self.assertEqual(engine_default('gpt-sovits', '/resources/external/gpt-sovits'), str(Path(temporary) / 'engines' / 'gpt-sovits'))
            self.assertEqual(tool_default('ffmpeg/bin', '/legacy/tools'), str(Path(temporary) / 'tools' / 'ffmpeg' / 'bin'))
            self.assertFalse((Path(temporary) / 'engines').exists())

    def test_source_defaults_remain_available_without_managed_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {}, clear=True):
            self.assertEqual(engine_default('seed-vc', temporary), temporary)

    def test_fresh_source_defaults_align_with_the_module_installation_home(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {}, clear=True), patch.object(Path, 'home', return_value=Path(temporary)):
            self.assertEqual(engine_default('seed-vc', str(Path(temporary) / 'missing')), str(Path(temporary) / '.openfabric-studio' / 'runtime' / 'engines' / 'seed-vc'))
