from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app import speaker_review_environment as environment
from app.speaker_review_worker import WEIGHTS_SHA256


class EnvironmentTests(unittest.TestCase):
    def test_verified_hash_probe_is_reused_only_while_file_identity_is_unchanged(self)->None:
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/"weights.ckpt";path.write_bytes(b"first")
            with patch.object(environment,"_hash",return_value=WEIGHTS_SHA256) as digest:
                self.assertEqual(environment.digest(path),WEIGHTS_SHA256)
                self.assertEqual(environment.digest(path),WEIGHTS_SHA256)
                self.assertEqual(digest.call_count,1)
                path.write_bytes(b"changed-size")
                self.assertEqual(environment.digest(path),WEIGHTS_SHA256)
                self.assertEqual(digest.call_count,2)

    def test_capability_inspects_metadata_without_importing_model_packages(self)->None:
        from app.speaker_review import capability
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);python=root/"bin"/"python";python.parent.mkdir();python.write_text("not launched")
            (root/"pyvenv.cfg").write_text("home = configured")
            site=root/"lib"/"python3.12"/"site-packages";site.mkdir(parents=True)
            for name,version in (("speechbrain","1.0.3"),("torch","2.6.0+cpu"),("torchaudio","2.6.0+cpu")):
                info=site/f"{name}-{version}.dist-info";info.mkdir();(info/"METADATA").write_text(f"Name: {name}\nVersion: {version}\n")
            result=capability(python_path=python,weights_path=root/"missing.ckpt")
            self.assertTrue(result.deps_available);self.assertFalse(result.available)
            self.assertEqual(result.reason,"speaker_weights_missing")

    def test_ready_encoder_without_ffmpeg_cannot_claim_workflow_available(self)->None:
        from app.speaker_review import capability
        from app.speaker_review_worker import EncoderIdentity
        identity=EncoderIdentity(weights_sha256=WEIGHTS_SHA256,speechbrain_version="1.0.3",torch_version="2.6.0",torchaudio_version="2.6.0")
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);weights=root/"weights.ckpt";weights.write_bytes(b"fixture")
            with patch.object(environment,"versions",return_value=("1.0.3","2.6.0","2.6.0")),patch.object(environment,"verified_identity",return_value=identity),patch("app.yue_upload.get_ffmpeg_bin",return_value=None):
                result=capability(python_path=root/"bin/python",weights_path=weights)
            self.assertFalse(result.available);self.assertEqual(result.reason,"speaker_ffmpeg_missing")
