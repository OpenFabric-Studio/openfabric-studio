"""Trial manifests distinguish real synthesis from mock placeholders."""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
from app.api.routes_speech_clone import get_speech_trial_manifest, get_speech_trial_provenance
from app.voice_profile_contracts import PatchSpeechVoiceProfileRequest
from app import speech_clone, voice_profiles, export_provenance
from app.voice_profile_contracts import SpeechCloneTrialRequest


class SpeechProvenanceTests(unittest.TestCase):
    def test_mock_trial_has_hash_bound_unknown_manifest_and_consent_checked_retrieval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch.object(voice_profiles,'PROFILES_ROOT',root/'profiles'),patch.object(speech_clone,'TRIALS_ROOT',root/'trials'),patch.dict('os.environ',{'OPENFABRIC_SPEECH_CLONE_MOCK':'1'}):
                speech_clone.start()
                reference=root/'reference.wav';speech_clone._write_silent_wav(reference)
                profile=voice_profiles.create_profile(name='Reader',consent_confirmed=True,audio_bytes=reference.read_bytes(),filename='reference.wav',reference_transcript='Reference.')
                trial=speech_clone.start_trial(SpeechCloneTrialRequest(profile_id=profile.id,text='Hello.'))
                if trial.output_path is None or trial.trial_id is None:raise AssertionError(trial.detail)
                media=Path(trial.output_path)
                self.assertTrue(export_provenance.path_for(media,'json').is_file(),'completed trial needs an adjacent retained manifest')
                manifest=export_provenance.read(media)
                self.assertEqual(manifest.content_origin,'unknown');self.assertEqual(manifest.artifact_sha256,export_provenance.digest(media))
                self.assertNotIn(str(root),manifest.model_dump_json())
                self.assertNotIn('Reference.',manifest.model_dump_json())
                get_speech_trial_provenance(trial.trial_id)
                export_provenance.path_for(media,'txt').unlink()
                get_speech_trial_manifest(trial.trial_id)
                self.assertTrue(export_provenance.path_for(media,'txt').is_file())
                voice_profiles.patch_profile(profile.id,PatchSpeechVoiceProfileRequest(consent_confirmed=False))
                for resolve in (get_speech_trial_provenance,get_speech_trial_manifest):
                    with self.assertRaises(HTTPException) as revoked:resolve(trial.trial_id)
                    self.assertEqual(revoked.exception.status_code,403)
                    self.assertEqual(revoked.exception.detail,'consent_required')


if __name__=='__main__':unittest.main()
