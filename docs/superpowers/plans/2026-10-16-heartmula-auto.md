# HeartMuLa Auto settings implementation plan

> Historical record — HeartMuLa retired on 16 October 2026. Its generation, training and installation instructions below no longer describe supported functionality. See [current music documentation](../../local-music.md).

> **For agentic workers:** Use focused agents with exclusive file ownership; the primary agent integrates and reviews the result.

**Goal:** Provide automatic HeartMuLa generation settings and raise duration only with real local evidence.

**Architecture:** Add an optional manual/auto request mode, normalize automatic seeds once before persisting jobs, and enforce shared duration/context bounds in both isolated workers. The backend advertises its tested HeartMuLa maximum; the Vue form uses it. Existing queue normalization follows that maximum. Preserve old manual jobs and all unrelated work.

**Tech stack:** Existing Python/FastAPI/Pydantic job ownership, isolated MLX/PyTorch workers, Vue/TypeScript and generated contracts. No new dependency.

- [x] Backend contracts/admission: regression tests for automatic random seed persistence, manual backward compatibility, Stable rejection and engine-specific bounds. Implement mode/defaults and advertise tested maximum. Generate contracts.
- [x] Worker boundaries: add stdlib duration/context helper, failing edge-case tests, then wire actual tokenizer counts into both MLX and LoRA paths before model loading; allow long codec input only within the shared bound.
- [x] Frontend: test and build HeartMuLa Auto default, visible ceiling/natural-end hint, manual override, mode/seed submission and history settings. Retain Stable behavior and protect stale/unmounted actions.
- [x] Queue: test and update HeartMuLa normalization to the same tested ceiling; preserve Stable/ACE/YuE behavior.
- [x] Mac evidence: verify app idle, test base and LoRA beyond 240 seconds without downloads, monitor actual frames/duration/memory, and keep artifacts outside Git. The base test decoded 483.92 seconds and the adapter test decoded exactly 480 seconds; enable the verified 480-second ceiling.
- [x] Integrate/review: run affected tests, full frontend tests/build, backend regression suite, generated contract check and all required mypy platform groups; perform browser verification while preserving drafts and active work. See `docs/heartmula-auto-proof.md` for actual duration, reload and check evidence. Independent review found and verified the Auto admission-order correction.

No commits, pushes, library migration or destructive cleanup.
