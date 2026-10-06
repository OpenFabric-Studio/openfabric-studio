"""CLI status and rejection paths remain offline and do not import models."""
from __future__ import annotations
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class SpeakerScriptTests(unittest.TestCase):
    def test_setup_accepts_explicit_managed_python_and_never_fetches_weights(self)->None:
        from scripts import setup_speaker_review as setup
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary,patch.object(setup.subprocess,"run") as run:
            run.return_value=subprocess.CompletedProcess([],0,stdout="Python 3.12.13",stderr="")
            target=Path(temporary)/"runtime";source=Path(sys.executable)
            result=setup.install(target,python_path=source)
            self.assertEqual(result,target/(".venv/Scripts/python.exe" if sys.platform=="win32" else ".venv/bin/python"))
            self.assertIn([str(source),"-m","venv",str(target/".venv")],[item.args[0] for item in run.call_args_list])
            commands=" ".join(str(item.args[0]) for item in run.call_args_list)
            self.assertNotIn("huggingface.co",commands);self.assertNotIn("embedding_model.ckpt",commands)

    def test_dependency_setup_refuses_existing_unowned_environment_without_launching(self)->None:
        from scripts import setup_speaker_review as setup
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary,patch.object(setup.subprocess,"run") as run:
            root=Path(temporary);(root/".venv").mkdir();(root/".venv"/"original.txt").write_text("keep")
            with self.assertRaisesRegex(ValueError,"speaker_setup_unowned"):
                setup.install(root,python_path=Path(sys.executable))
            run.assert_not_called();self.assertEqual((root/".venv"/"original.txt").read_text(),"keep")

    def test_dependency_setup_resumes_only_matching_owned_marker(self)->None:
        from scripts import setup_speaker_review as setup
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary,patch.object(setup.subprocess,"run") as run:
            run.return_value=subprocess.CompletedProcess([],0,stdout="Python 3.12.13",stderr="")
            root=Path(temporary);(root/".venv").mkdir();original=root/".venv"/"original.txt";original.write_text("partial")
            marker=root/".openfabric-speaker-runtime.json"
            marker.write_text(json.dumps({"schema_version":1,"dependency_setup":"speechbrain-1.0.3-torch-2.6.0-cpu-v1"}))
            setup.install(root,python_path=Path(sys.executable))
            self.assertEqual(original.read_text(),"partial")
            self.assertNotIn("--clear"," ".join(str(item.args) for item in run.call_args_list))

    def test_setup_rejects_base_prefix_and_unsupported_python_without_pip(self)->None:
        from scripts import setup_speaker_review as setup
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary,patch.object(setup.subprocess,"run") as run:
            root=Path(temporary)
            with patch.object(setup.sys,"prefix",str(root/".venv")):
                with self.assertRaisesRegex(ValueError,"speaker_setup_path_invalid"):setup.install(root)
            run.assert_not_called()
            run.return_value=subprocess.CompletedProcess([],0,stdout="Python 3.11.13",stderr="")
            with self.assertRaisesRegex(ValueError,"speaker_python_unsupported"):setup.install(root,python_path=Path(sys.executable))
            self.assertEqual(run.call_count,1);self.assertFalse((root/".venv").exists())
    def test_status_requires_explicit_weights_without_model_import_or_download(self)->None:
        script=Path(__file__).resolve().parents[1]/"scripts"/"speaker_review.py"
        self.assertTrue(script.is_file(),"optional offline runner must be shipped")
        result=subprocess.run([sys.executable,str(script),"--capability"],capture_output=True,text=True,timeout=5,check=True)
        payload=json.loads(result.stdout)
        self.assertFalse(payload["available"]);self.assertIn("speaker_",payload["reason"])

    def test_unreviewed_checkpoint_is_rejected_before_model_import(self)->None:
        script=Path(__file__).resolve().parents[1]/"scripts"/"speaker_review.py"
        self.assertTrue(script.is_file(),"optional offline runner must be shipped")
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);weights=root/"weights.ckpt";weights.write_bytes(b"unreviewed pickle")
            request=root/"request.json";request.write_text(json.dumps({"weights":str(weights),"reference":str(root/"ref.wav"),"passage":str(root/"target.wav")}))
            result=subprocess.run([sys.executable,str(script),"--request",str(request),"--output",str(root/"output.json")],capture_output=True,text=True,timeout=5)
            self.assertNotEqual(result.returncode,0);self.assertFalse((root/"output.json").exists())
            self.assertNotIn("Traceback",result.stdout+result.stderr)
            self.assertIn("speaker_weights_unverified",result.stdout)
