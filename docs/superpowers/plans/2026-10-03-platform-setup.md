# Platform Setup Reliability Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development to implement this plan task by task. Preserve contributor changes and leave work uncommitted.

**Goal:** Open desktop after minimal setup, preserve selected engine paths and make unsupported or incomplete installation explicit.

**Architecture:** Retain desktop's managed uv/backend bootstrap while moving feature installation into the backend wizard. Harden current process/extraction/state ownership and share managed module layout. Source adapters refresh locked requirements consistently across Windows/macOS/Linux.

**Tech Stack:** Electron/Node, managed uv/Python, Python CLI adapters, existing pinned manifests.

## Tasks

- [x] Reproduce unsupported `describePlan` crashes and partial installation completion in desktop tests. Assert Linux/Intel macOS yields useful compatibility results before accessing absent assets.
- [x] Modify `desktop/src/bootstrap/components.js`, `run.js`, `paths.js`, `server.js` and tests for minimal uv/backend bootstrap and optional writable Seed-VC/GPT/LTX/RoFormer roots. Verify packages after installation before recording completion.
- [x] Harden `desktop/src/proc.js`, extraction and setup state; await cancellation/shutdown and preserve old artifacts through interrupted replacement. Add cancellation/descendant tests.
- [x] Provision or explicitly guide installation of both FFmpeg and FFprobe using verified sources; never mark a single executable sufficient. Do not invent Linux desktop assets or hashes.
- [x] Inspect source launchers and optional setup scripts; add platform-aware adapters using Scripts/python.exe on Windows and bin/python on POSIX, immutable source identities and honest manual weight steps. Refresh backend locked requirements on upgrades.
- [x] Run desktop tests and applicable backend checks. Update README and setup docs with one launch/setup route per platform, supported hardware and explicit platform verification gaps.
