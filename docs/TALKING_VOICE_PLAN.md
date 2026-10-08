# Talking voice + audiobooks MVP (GPT-SoVITS)

**Priority (Seth):** normal / talking voice clone and audiobooks **first**. Singing stays Seed-VC. Characters / video / LoRA are later.

**License gate:** GPT-SoVITS is **MIT** ([RVC-Boss/GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)); OpenFabric owns the FastAPI + SQLite store. This implementation did not copy VoiceStudio or LocalAI source. Since the 2026-10-08 AGPL transition, any proposed source reuse must be reviewed for exact license compatibility and retain provenance and notices; an AGPL label alone does not clear model weights or other assets.

**Attribution:** keep GPT-SoVITS + LocalAI lines in repo `NOTICE`. Optional install is never auto-bundled with weights.

---

## Current baseline (2026-10-02, Phase C audiobooks)

| Piece | State |
|-------|--------|
| Speech voice profiles API + UI | Done (`/api/voice-profiles`, Voice page panel, consent + wav/flac) |
| `POST /api/speech-clone/trials` | Detect + mock + real HTTP invoke via GPT-SoVITS `api.py` (`POST /` @ `:9880`) |
| Engine statuses | `engine_not_installed` / `api_unavailable` / `completed` / `failed` / `mock_completed` |
| Seed-VC | Singing only |
| Audiobooks | Phase C: serial chapter synth + ffmpeg export + download + basic UI |
| GPT-SoVITS checkout | Optional `external/gpt-sovits` via `./setup_speech.sh` |
| GPT-SoVITS weights | **Not** downloaded by OpenFabric — manual (see setup_speech.sh output) |

---

## MVP phases

### Phase A — Engine detect + worker interface (this pass)

1. Env / path detection (no downloads):
   - `OPENFABRIC_GPT_SOVITS_DIR` or `GPT_SOVITS_DIR` (default `external/gpt-sovits`)
   - Treat as installed when checkout markers exist (e.g. `GPT_SoVITS/` package or upstream `api.py` / `webui.py`).
2. Mock / dry-run for tests and CI:
   - `OPENFABRIC_SPEECH_CLONE_MOCK=1` → write a tiny valid WAV under data dir, return `mock_completed` (HTTP 200).
3. Missing engine → structured `engine_not_installed` + install hints (HTTP 501), never crash.
4. Engine present but synthesis HTTP not wired yet → `engine_ready` (HTTP 200) with clear detail (next pass invokes API / CLI).
5. `GET /api/speech-clone/engine` for UI status.

### Phase B — Speech trial → real WAV (needs manual model setup)

1. User runs `./setup_speech.sh` (clone + Python 3.11 venv) or sets `OPENFABRIC_GPT_SOVITS_DIR`, then downloads **their** pretrained weights (upstream README / HF).
2. Start pinned local API from the checkout:
   - Entrypoint: **`api.py`** (not api_v2)
   - Bind: `python api.py -a 127.0.0.1 -p 9880 -d cpu`
   - Override base URL with `OPENFABRIC_GPT_SOVITS_API_URL` (default `http://127.0.0.1:9880`)
3. OpenFabric worker `POST`s JSON to `/` with:
   - `refer_wav_path` = consent-backed profile reference audio
   - `prompt_text` = trial `prompt_text` or profile `notes` (fallback `"Reference audio."`)
   - `prompt_language` / `text_language` (default `en`)
   - `text` (≤ ~8k chars per trial)
4. Store trial WAV under `DATA_DIR/speech-clone-trials/`; return path. If checkout exists but API is down → `api_unavailable` (HTTP 200, no crash).
5. Minimal Voice-page UI: pick profile → paste text → Generate → play / show install hints.

**Manual setup:**

```bash
./setup_speech.sh
# then download pretrained models into external/gpt-sovits/GPT_SoVITS/pretrained_models
# (see script footer / https://huggingface.co/lj1995/GPT-SoVITS)
export OPENFABRIC_GPT_SOVITS_DIR=/absolute/path/to/openfabric-studio/external/gpt-sovits
export OPENFABRIC_GPT_SOVITS_API_URL=http://127.0.0.1:9880
cd "$OPENFABRIC_GPT_SOVITS_DIR" && source .venv/bin/activate && python api.py -a 127.0.0.1 -p 9880 -d cpu
```

`setup_speech.sh` clones + venv + pip deps and documents weight steps **without** fetching multi-GB blobs.

### Phase C — Audiobook v1

1. **Stub:** `POST /api/audiobooks` creates a book + one queued job per chapter. `GET` list books / jobs. **Done.**
2. **Worker:** for each chapter job, call speech-clone worker (same engine as trials); write per-chapter WAV under `DATA_DIR/audiobooks/<book_id>/chapters/`. **Done** (serial thread; `OPENFABRIC_AUDIOBOOK_SYNC=1` for tests).
3. **Export:** concatenate chapter WAVs (ffmpeg) → `export.wav`; `GET /api/audiobooks/{id}/export` + chapter audio. Retry failed chapters via `POST .../retry`. **Done** (pause deferred).
4. **UI:** Audiobook panel on Voice page — title, profile, chapter paste, job list, download. **Done** (no `.txt` upload yet).

Chapter limits (v1): ≤20k chars/chapter, ≤100 chapters; queue serially on one worker to avoid OOM.

### Phase D — Hardening (after real synth works)

- Consent re-check on every job; refuse profiles without consent.
- Disk quotas / cleanup for trial + chapter audio.
- Progress events (reuse voice job progress patterns if useful).
- Daily code review covers new routes (path traversal, oversized text, auth assumptions — local-first app).

---

## Out of scope for this MVP

- Additional speech engines, including VoiceStudio
- Auto-download of GPT-SoVITS pretrained weights in OpenFabric CI or setup
- Character consistency / image / video / kohya LoRA
- Cloud TTS APIs

---

## Success criteria

| Milestone | Done when |
|-----------|-----------|
| A | Detect + mock paths tested; missing engine returns clean 501; plan + NOTICE OK |
| B | Profile + text → real WAV with user-installed GPT-SoVITS |
| C stub | Create book from chapters; list jobs (`queued`) |
| C full | Chapters synthesize + concatenated export plays (**this pass**, mock + live) |

---

## Implementation map (repo)

| Area | Paths |
|------|--------|
| Plan (workspace) | `/workspace/openfabric/TALKING_VOICE_PLAN.md` |
| Worker | `backend/app/speech_clone.py` |
| Contracts | `backend/app/voice_profile_contracts.py`, `audiobook_contracts.py` |
| Routes | `routes_speech_clone.py`, `routes_audiobooks.py` |
| Store | `backend/app/audiobooks.py` |
| UI | `VoiceProfilesPanel.vue` (+ locales) |
| Setup (no weights) | `setup_speech.sh` |
| Attribution | `NOTICE` |

Singing voice clone remains Seed-VC; do not conflate profile IDs with Seed-VC voice workspace IDs.
