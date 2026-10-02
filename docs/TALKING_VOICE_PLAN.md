# Talking voice + audiobooks MVP (GPT-SoVITS)

**Priority (Seth):** normal / talking voice clone and audiobooks **first**. Singing stays Seed-VC. Characters / video / LoRA are later.

**License gate:** GPT-SoVITS is **MIT** ([RVC-Boss/GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)). Do **not** vendor or copy [VoiceStudio](https://github.com/JarodMica/VoiceStudio) (AGPL). UX ideas only from LocalAI (MIT); OpenFabric owns the FastAPI + SQLite store.

**Attribution:** keep GPT-SoVITS + LocalAI lines in repo `NOTICE`. Optional install is never auto-bundled with weights.

---

## Current baseline (2026-10-02)

| Piece | State |
|-------|--------|
| Speech voice profiles API + UI | Done (`/api/voice-profiles`, Voice page panel, consent + wav/flac) |
| `POST /api/speech-clone/trials` | Scaffold → becoming worker interface (detect / mock / real) |
| Seed-VC | Singing only |
| Audiobooks | Stub API in this pass; no concat export yet |
| GPT-SoVITS weights | **Not** downloaded by OpenFabric |

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

1. User clones GPT-SoVITS beside the app (or sets env), runs upstream setup + downloads **their** pretrained weights.
2. OpenFabric worker calls a stable local API (prefer GPT-SoVITS HTTP API once pinned) with:
   - reference audio from consent-backed profile
   - text (≤ ~8k chars per trial)
3. Store trial WAV under `DATA_DIR/speech-clone-trials/`; return path / download URL.
4. Minimal Voice-page UI: pick profile → paste text → Generate → play / show install hints.

**Manual setup (not automated here):**

```bash
# Example only — user-owned checkout + weights
git clone https://github.com/RVC-Boss/GPT-SoVITS.git external/gpt-sovits
# follow upstream README for venv + pretrained model download
export OPENFABRIC_GPT_SOVITS_DIR=/absolute/path/to/gpt-sovits
```

Optional later: `setup_speech.sh` that clones the repo and documents weight steps **without** fetching multi-GB blobs in CI.

### Phase C — Audiobook v1

1. **Stub (this pass):** `POST /api/audiobooks` creates a book + one queued job per chapter (text from paste or chapter list). `GET` list books / jobs. No synthesis yet.
2. **Worker:** for each chapter job, call speech-clone worker (same engine as trials); write per-chapter WAV.
3. **Export:** concatenate chapter WAVs (ffmpeg) → single audiobook file; expose download. Pause / retry failed chapters.
4. **UI (follow-up):** Audiobook panel — title, profile, chapter paste or `.txt` upload, job list, export button.

Chapter limits (v1): reasonable text caps per chapter; queue serially on one GPU to avoid OOM.

### Phase D — Hardening (after real synth works)

- Consent re-check on every job; refuse profiles without consent.
- Disk quotas / cleanup for trial + chapter audio.
- Progress events (reuse voice job progress patterns if useful).
- Daily code review covers new routes (path traversal, oversized text, auth assumptions — local-first app).

---

## Out of scope for this MVP

- VoiceStudio / any AGPL speech stack
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
| C full | Chapters synthesize + concatenated export plays |

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
| Attribution | `NOTICE` |

Singing voice clone remains Seed-VC; do not conflate profile IDs with Seed-VC voice workspace IDs.
