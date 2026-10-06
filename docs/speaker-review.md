# Optional local speaker screening

This optional tool compares two saved audio recordings using a local CPU encoder. It provides a cosine-similarity hint for listening review, not a probability or proof of identity. It does not synthesize speech, contact a provider, accept a take or automatically repair a passage.

## Install the separate CPU environment

Use Settings → Setup & modules → Speaker review to review license declarations and install the dedicated dependencies. Model downloads selected elsewhere in the wizard do not download speaker weights. The module stays incomplete until a reviewed local checkpoint is explicitly configured.

For manual source installations, use the existing **Python 3.12** backend interpreter to create a separate runtime. Do not install these dependencies into the backend environment.

macOS/Linux, from the repository root:

```sh
backend/.venv/bin/python backend/scripts/setup_speaker_review.py \
  --runtime-root "$HOME/.openfabric-studio/runtime/engines/SpeakerReview" \
  --python backend/.venv/bin/python
```

Windows PowerShell:

```powershell
backend\.venv\Scripts\python.exe backend\scripts\setup_speaker_review.py `
  --runtime-root "$env:USERPROFILE\.openfabric-studio\runtime\engines\SpeakerReview" `
  --python backend\.venv\Scripts\python.exe
```

The setup pins SpeechBrain 1.0.3, Torch 2.6.0 and torchaudio 2.6.0. Linux/Windows use the official PyTorch CPU wheel index; macOS uses the corresponding PyPI packages. An existing unrelated environment, the backend environment and symlinked installation roots are refused. An owned interrupted installation can be retried. Native clean installs on all three operating systems have not been demonstrated by the mocked setup tests.

## Review and configure the checkpoint

Review the official [SpeechBrain ECAPA VoxCeleb model card](https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb) and obtain its `embedding_model.ckpt` explicitly. The reviewed file is approximately 83.3 MB and must have SHA-256:

```text
0575cb64845e6b9a10db9bcb74d5ac32b326b8dc90352671d345e2ee3d0126a2
```

The runner rejects a different file. The publisher's Apache-2.0 declaration covers the linked model; source recordings and the user's reference recordings have separate rights/permissions. No checkpoint or remote YAML is fetched by status checks, setup or analysis.

Set these variables for the backend process (or in its existing private environment configuration), then restart that process:

```text
OPENFABRIC_SPEAKER_REVIEW_PYTHON=/absolute/path/to/SpeakerReview/.venv/bin/python
OPENFABRIC_SPEAKER_REVIEW_WEIGHTS=/absolute/path/to/embedding_model.ckpt
```

On Windows the interpreter is `SpeakerReview\.venv\Scripts\python.exe`. Paths are local operator configuration, not media sent to a provider. Refresh Settings status after configuration. Metadata/dependency status does not import Torch; the analysis child verifies the actual checkpoint bytes and loads it with `weights_only=True` into the fixed inspected ECAPA architecture.

## Review a saved passage

1. Open an audiobook's **Review passages**, then **Local speaker screening**.
2. Select a different accepted passage or completed cast audition for the current voice. Listen and explicitly approve the reference.
3. Enter a threshold calibrated with representative recordings you have reviewed. There is no universal default threshold.
4. Run the local comparison. Listen to results below your chosen threshold and use the existing fresh-take workflow when needed.

The encoder receives 16 kHz mono PCM. Analysis is bounded to 30 seconds; material shorter than three seconds or with insufficient amplitude activity is skipped. Activity screening is not speech detection. The record identifies analyzed durations (up to the first 30 seconds), encoder/checkpoint/preprocessing/package fingerprints, current cast/profile, accepted render/take and reference/target audio hashes. Incompatible encoder identities cannot be compared even if dimensions match.

Private persisted 192-dimensional embeddings are voice-derived data. They remain in the local review storage; media and vectors are not sent to a remote provider.

Known changes make historical hints stale. Completed hints are periodically revalidated while the panel is open. Reloading/navigation does not abandon backend work; cancellation waits for owned subprocess cleanup. Corrupt optional records are skipped safely and unverified worker receipts are quarantined rather than killing an arbitrary PID.

GPT-SoVITS does not attest its loaded checkpoint. Local comparisons show that unverified renderer identity explicitly. The exact saved audio comparison remains useful, but an external checkpoint switch cannot be detected from the server address; reselect the intended reference and review after such changes.

## Verification limits

The workflow is covered with isolated audio and model-boundary fixtures, genuine owned CPU child errors, cancellation/recovery cases and frontend revalidation tests. A real ECAPA run has not been verified in this task because optional encoder dependencies/weights were not installed. No real voice-identification accuracy, universal threshold, language performance or native platform installation result is claimed.
