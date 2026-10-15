# Automatic music setup

**Goal:** Make the automatic music setup finish installation and required model placement, rather than return a generic manual preview instruction after package checks.

**Scope:** Existing reviewed setup jobs, installed external music environments, managed ACE-Step/HeartMuLa/Stable Audio 3 installs, model download defaults, progress and documentation. Preserve existing data and source checkouts. Model quality and GPU performance remain separate from installation success.

**Approach:** Use immutable public model revisions and checked file sizes/digests, the existing resumable downloader and owned subprocess lifecycle, isolated conversion staging, and exclusive model publication. Reuse valid installed model bundles. Existing external environments may be checked and receive missing model files; their source and environment are never reset or overwritten. Setup retains explicit plan review before installation.

- [x] Regression tests for external ACE completion, selected model/download consent, managed music model setup, disk admission and resumable detail updates.
- [x] Pinned model manifest and installation adapter with preservation, failure, cancellation and interrupted publication tests.
- [x] Integrate model setup into existing and fresh music setup, with detailed durable progress and separate installation/GPU readiness.
- [x] Default automatic wizard to required model downloads; explain actual scope before Start setup and distinguish completion from audio quality.
- [x] Run focused regressions, frontend tests/build, backend suite, all-platform strict typing, contracts and affected desktop packaging checks.
- [x] Inspect actual existing ACE model bundle and run reviewed setup verification when app work is idle; do not redownload existing models or run inference as an incidental test.

Actual browser flow on the restarted idle app: Automatic setup → automatically selected ACE-Step with required models included → Review requirements (zero missing download bytes) → Start setup. Saved job `dbdb40f32320462d864f7532e41fc712` completed in approximately 1.2 seconds, with Media tools and ACE-Step both verified and no manual steps. All 36 existing files in the selected XL-SFT/4B/VAE/embedding folders retained identical sizes and modification times. Screenshot: `/tmp/openfabric-automatic-setup-complete.png`.

Independent review found and verified fixes for separate-volume disk admission and download scope growth on Resume. Automated checks cover synthetic download, conflict, cancellation, conversion, publication and recovery boundaries; no fresh multi-gigabyte model download, live MLX conversion or GPU inference benchmark was run.

Final verification: 99 focused backend regressions; full backend suite 2,074 tests, 10 skipped; frontend 165 files / 1,552 tests; strict TypeScript and production build; desktop 87 passed / 1 platform skip; all 21 CI mypy invocations for Linux/macOS/Windows; generated contract drift and diff whitespace checks. Tests used temporary configuration, library, module and Seed-VC roots. The running source app remains available on loopback ports 5173/9000.

No commit or push is authorized. Other modules keep explicit, honest limitations where no reviewed automatic adapter exists.
