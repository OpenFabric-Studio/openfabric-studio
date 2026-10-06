"""Optional encoder boundary tests do not import Torch or download weights."""
from __future__ import annotations
import math
import tempfile
import unittest
import wave
from pathlib import Path
from app import speaker_review_worker as worker


class SpeakerWorkerTests(unittest.TestCase):
    def test_equal_dimensions_do_not_make_different_encoders_compatible(self) -> None:
        first=worker.EncoderIdentity(weights_sha256="a"*64, speechbrain_version="1.0.3", torch_version="2.6.0", torchaudio_version="2.6.0")
        second=worker.EncoderIdentity(weights_sha256="b"*64, speechbrain_version="1.0.3", torch_version="2.6.0", torchaudio_version="2.6.0")
        vector=[1.0]+[0.0]*191
        with self.assertRaisesRegex(ValueError,"speaker_encoder_changed"):
            worker.compare(worker.Embedding(first,vector,3000,3000),worker.Embedding(second,vector,3000,3000))

    def test_finite_nonzero_vectors_are_required_and_score_is_cosine_not_probability(self) -> None:
        identity=worker.EncoderIdentity(weights_sha256="a"*64,speechbrain_version="1.0.3",torch_version="2.6.0",torchaudio_version="2.6.0")
        positive=worker.Embedding(identity,[1.0]+[0.0]*191,3000,3000)
        negative=worker.Embedding(identity,[-1.0]+[0.0]*191,3000,3000)
        self.assertEqual(worker.compare(positive,negative),-1.0)
        for vector in ([0.0]*192,[math.nan]+[0.0]*191,[math.inf]+[0.0]*191,[True]+[0.0]*191,[1.0]*191):
            with self.assertRaises(ValueError):
                worker.vector(vector)

    def test_silent_short_and_truncated_audio_are_not_speech_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/"input.wav"
            with wave.open(str(path),"wb") as audio:
                audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(16000);audio.writeframes(b"\0\0"*64000)
            with self.assertRaisesRegex(ValueError,"speaker_audio_insufficient"):
                worker.read_pcm(path)
            with wave.open(str(path),"wb") as audio:
                audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(16000);audio.writeframes(b"\xff\x1f"*16000)
            with self.assertRaisesRegex(ValueError,"speaker_audio_insufficient"):
                worker.read_pcm(path)
            path.write_bytes(path.read_bytes()[:-100])
            with self.assertRaises(ValueError):
                worker.read_pcm(path)

    def test_pipeline_identity_and_protocol_are_bounded_and_reject_extra_fields(self) -> None:
        identity=worker.EncoderIdentity(weights_sha256="a"*64,speechbrain_version="1.0.3",torch_version="2.6.0",torchaudio_version="2.6.0")
        embedding=worker.Embedding(identity,[1.0]+[0.0]*191,3000,3000)
        self.assertEqual(worker.parse_embedding(embedding.to_json()),embedding)
        invalid=embedding.to_json();invalid["secret"]="not accepted"
        with self.assertRaises(ValueError):worker.parse_embedding(invalid)
