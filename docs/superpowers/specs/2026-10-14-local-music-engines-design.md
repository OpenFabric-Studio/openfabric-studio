# Additional local music engines

The maintainer selected HeartMuLa and Stable Audio 3 after reviewing
their uses, Mac runtimes and license differences. Add those engines to Music
without changing the ACE-Step/YuE orchestrator or introducing a service layer.

## Behavior

- HeartMuLa uses the reviewed Apple MLX port; Stable Audio 3 uses the official
  MLX implementation.
- Separate engine virtual environments keep GPU dependencies out of the API.
- Navigation never starts inference. The form exposes only verified generation
  controls, with engine-specific duration and lyrics validation.
- Setup appears in the existing module catalog, with immutable source pins,
  license cards and explicit local-weight instructions. Package installation
  does not download weights; generation is offline.
- Backend-owned jobs survive browser navigation/reload. Workers share existing
  resource admission and process supervision. Cancellation and shutdown drain
  descendants before removing incomplete output.
- Validate a complete PCM WAV before atomic publication into the shared track
  library. Retain request provenance; recovery must not generate again or insert
  a duplicate track. Unverified process identities block conflicting work.
- Missing or unsupported engines produce stable safe errors and setup links.

## Verification

Use temporary libraries/configuration/engine paths and mocked model boundaries.
Cover request validation, generation/publication, failed or truncated output,
cancellation, concurrency, teardown and interrupted recovery. Run frontend
tests/build, backend regressions, generated-contract drift checks, the CI strict
Python platform loop and desktop tests. Check the running UI/API after restart.
GPU speed and perceptual quality require actual inference evidence separately.

## Alternatives and scope

Reusing a persistent model server would add lifecycle and dependency complexity
for these upstream command-line runtimes. Extending the ACE/YuE orchestrator
would require unsupported server protocols. Use one owned subprocess per job.
No DiffRhythm (excluded by the maintainer for non-commercial conditioning
weights), MusicGen, cloud generation changes, LoRA training, cover/remix workflows,
automatic weight downloads or new runtime dependencies in the API.
