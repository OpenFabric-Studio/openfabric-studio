# ACE-Step LoRA state and errors

**Goal:** Fix misleading load/strength errors and prevent generation with an adapter different from the user's confirmed choice.

**Evidence:** The runtime successfully loaded Inspiration-1 and accepted strength 0.75, while later loads returned HTTP 400. The form retries every failure against nested paths, retains old errors after successful strength changes, never reads native LoRA status and permits submission after a failed selection.

**Design:** Keep existing native URL paths, normalize only ACE LoRA responses into bounded schema-owned status and stable safe error codes. Serialize LoRA mutations and generation status/submission with one shared backend lock; reject adapter changes while app-owned ACE generation is active. The form reads actual status, offers the currently loaded adapter explicitly, retries nesting only for path/layout failures, serializes edits, and blocks generation until its selection and strength are verified. No automatic unloading/replacement on failure, no raw vendor errors, no model-file changes or retraining.

- [x] Add failing regressions for safe error classification, status validation, stale/duplicate selections, recovery after scale failure, teardown and generation mismatch.
- [x] Implement bounded native LoRA bridge/contracts and proxy integration; regenerate client contracts.
- [x] Validate expected adapter/strength under the same lock at generation submission, preserving existing legacy requests without snapshots.
- [x] Implement the smallest coherent form state handling, actual-active feedback, explicit current-adapter selection and translated failures/retry.
- [x] Review integration; run frontend tests/build, backend regressions, contracts, CI typing and affected desktop tests. Browser verification must not interrupt active user work or modify model files.

No commit or push requested. Preserve all existing uncommitted work and user adapters.

Review also found native scale warnings that change reported metadata before applying weights, generation selectors that can bypass the primary handler's adapter, and model initialization racing generation preflight. The bridge rejects unsuccessful scale acknowledgements; explicit adapter confirmation always applies strength, the generation guard validates model aliases against fresh primary inventory, and initialization retains the shared lock through response cleanup. The form captures request settings, style audio and voice selection before its asynchronous verification.

Verification: frontend 1,608 tests and strict build; full backend 2,130 tests (10 skipped), followed by 132 focused regressions after final backend refinements; desktop 87 passed (1 skipped); all 21 CI typing commands plus final changed Python scopes across Linux/macOS/Windows; generated contracts check and diff whitespace check. Tests used isolated configuration/library/engine/module paths and mocked model boundaries.

Live verification found the original browser registry entry pointed at a missing `Operations/adapter` folder. The existing exported `Inspiration-1/adapter` contained its config and safetensors weights. Added and successfully loaded the corrected registration before removing its stale duplicate. The runtime initialized its configured XL SFT primary, loaded `Insp-1`, applied strength 0.75 to all 256 targets with no skipped targets, and confirmed active state. Reuse through the current-adapter option and registry persistence/reconfirmation after reload both passed; Generate was enabled with no alerts. No song was generated and adapter files were unchanged.


## Native initialization limits verified during testing

- Fresh ACE startup is lazy: health exposes `models_initialized: false` while `loaded_model` can already contain the configured primary name. Read-only status reports no adapter only after validating this exact health state; initialized or uncertain states remain blocked.
- Explicit adapter loading may initialize the configured primary through the existing `/v1/init` contract with `init_llm: false`, then retry loading once. Native generation initializes its configured LM later as needed. No read-only status call initializes models.
- Auto-initialization drains its owned, bounded HTTP request before propagating cancellation and releasing control/dataset locks. The native executor does not cancel its blocking initialization when an HTTP request disconnects; if the 300-second request times out, completion of native initialization remains unverified. Existing raw `/v1/init` has the same executor limit. No new global quarantine was added in this bug fix.
- The native explicit init path (`acestep/api/http/model_init_service.py:111-128`) sets initialization flags but does not capture `_service_init_kwargs` and `_checkpoint_dir` like `acestep/api/startup_model_init.py:99-108`. A later native on-demand base-model switch can fall back to the primary (`acestep/api/job_model_selection.py:72-73,178-180`) after explicit initialization. This existing compatibility limit is outside this adapter-state fix; live verification uses the configured primary. Vendor files are unchanged.
