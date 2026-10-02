# Voice workflow verification

Completed in the existing checkout without commits. Existing unrelated work was preserved.

## Actual checks

- Backend: 239 tests passed using `/tmp/openfabric-review-env.9vBA33/venv/bin/python -m unittest discover -s app -p '*_test.py' -q` with isolated `OPENFABRIC_CONFIG` and `OPENFABRIC_DATA_DIR`.
- Frontend: 142 tests passed; `npm run build` passed the explicit strict-type policy, Vue/TypeScript check and production bundle.
- Desktop: 27 existing startup/installer tests passed.
- Strict mypy: the 20-file CI scope passed, including coverage, preparation, contracts and the RoFormer setup helper. This is a scoped check, not a claim that the entire legacy backend is checked.
- Generated contract drift, shell syntax (`setup_voice.sh`, `setup_roformer.sh`, `dev.sh`) and `git diff --check` passed.
- Peer review found and verified fixes for optional coverage cancellation withdrawing build readiness and expected playback AbortError being displayed as a failure.

## Browser and runtime evidence

An isolated library contained synthetic original/cleaned FLOAT WAV passages. Browser checks verified five tabs, keyboard navigation/focus, visible-source bulk changes, draft persistence across tabs, save gates, actual native WAV playback, coverage polling/results, and a 375px layout without horizontal overflow. No browser page errors or Vite overlay were detected in the final pass.

The real coverage endpoint measured all 23 selected two-second clips without separation: 43.24 seconds of reliable pitch frames, observed central span 220–440 Hz. The chart labeled intermediate notes not observed. Saving a 15→30-minute budget retained the same 23 sample IDs and cached measurements, updated the budget to 1800 seconds and rotated the review revision.

A separate root verification invoked the actual pinned RoFormer adapter on a synthetic two-second stereo fixture. MPS inference completed in 2.497 seconds and emitted 88200 finite stereo frames at 44100 Hz; the original SHA256 was unchanged. The helper had also verified the author checkpoint's fixed SHA256 and matching upstream configuration. This is execution evidence, not a real-song separation-quality benchmark or a performance promise for long files.

The temporary app processes were stopped after verification. Production RoFormer configuration lives in the ignored backend dotenv; restart normal app processes to pick it up.

## Remaining empirical limits

No real-song separation quality, noisy-vocal pitch accuracy, perceptual improvement from fine-tuning or optimal singer-specific training duration was benchmarked. Vowels, phoneme coverage, full singer range, register technique and identity are not inferred by the automatic coverage report. Spectrum measurements describe prepared audio and cannot establish the uploaded recording's original bandwidth or recover missing detail. Use held-out listening comparisons for those decisions.
