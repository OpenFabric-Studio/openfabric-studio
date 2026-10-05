# Cast repair and dialogue reels

Approved direction: implement all practical packages from the 5 October research assessment. Preserve existing libraries, running processes, model caches and fork attribution. Automatic ASR retries, experimental lip-sync, latent preview completion, VLM ranking and additional engine collections remain deferred.

## Deliverables

1. Snapshot acoustic render inputs, pass actual speech languages, fingerprint references and known engine identities, and bypass cache on explicit redo. Consent remains a live requirement.
2. Audition representative cast lines and a short scene using production speech inputs.
3. Expose stable completed passages with timestamps and revisions. Generate a fresh repair candidate, audition it and accept it atomically; retain accepted audio on failure and reject stale candidates.
4. Review DOCX and long SRT/VTT imports before synthesis, preserve sources/cue provenance and require speaker mapping. Bound archives and parsers.
5. Provide configurable pause analysis and opt-in local ASR review with persisted passage-version flags and explicit unavailable/failure states. Human review decides repairs.
6. Create persisted dialogue reels from selected cast passages with timed captions, silent anchored video and the original cast soundtrack. State existing duration/shot limits explicitly.
7. Improve character training with reviewed captions, dataset/settings/base-model provenance, compatibility validation and separate held-out comparisons.
8. Investigate the upstream LTX release and provide a reproducible hardware benchmark. Upgrade only with compatibility evidence; record unavailable hardware/models honestly.
9. Make caption fonts predictable and persist media-aware undo, preserving variant and approval references.

## Ownership

- Audiobook worker: audiobook/profile/speech backend, contracts and routes, repair and audition services/tests.
- Import/QA worker: document/subtitle parsers and review UI, pause analysis, local ASR service/contracts/router/tests. Coordinate audiobook contract changes with its owner.
- Video worker: video backend/frontend, character trainer, deterministic captions, persisted undo and LTX benchmark/tests.
- Coordinator: audiobook frontend/API client, generated contracts, application lifecycle integration, translated strings, integration tests, final review and verification.

## Execution and verification

Implement focused regressions before fixes. Test with temporary configuration/data/module roots and a temporary Seed-VC engine; model boundaries are mocked in unit tests. Reuse existing dependency installations, not user configuration or models. Verify schema generation, strict frontend types/build/tests, backend regressions, the CI platform typing loop, and desktop checks when startup or layout is affected. Browser-check the integrated flows against an isolated backend with synthetic fixtures. Review final diff for path confinement, atomic publication, subprocess ownership, stale UI responses, migration safety and retained original media.

Hardware results require actual measurements. Do not infer quality, lip-sync or platform support from mocked tests. No commits or publication are part of this implementation request.
