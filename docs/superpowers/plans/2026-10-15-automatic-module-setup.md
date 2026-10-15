# Automatic setup wizard

**Goal:** Let setup choose relevant modules automatically so the user can review and install without knowing engine names.

**Design:** Reuse the existing computer → selection → review → install wizard. Preselect the explicitly requested feature from its module card or valid `#module-…` Settings context; otherwise use a backend-owned starter suggestion. Never silently replace an unavailable requested feature. Show the actual selection and reason; put the full grouped checklist under Customize. Preserve a reset-to-automatic action and existing reviewed installation, dependencies, licensing and saved job controls.

**Starter policy:** Suggest one supported music engine, preferring an existing ready installation, then installed, then ACE-Step/YuE/Stable Audio/HeartMuLa catalog order. Use Media tools if no supported music engine is available. The policy describes the supported integration, not measured GPU fit. It must not suggest multiple competing engines or optional voice/video tools by default.

**Boundaries:** Recommendations are read-only backend inventory data. The UI validates requested context against current supported inventory and never guesses unsupported IDs. Module dependencies are expanded by the existing backend plan. Download selection and the review/start boundary remain explicit. Existing Stable Audio/HeartMuLa setup does not fetch their model files; explain this before review. No installation, download or model start is authorized as part of development verification.

- [x] Add backend recommendation DTO/helper/tests and regenerate client contracts.
- [x] Write failing UI tests for default/context/card selections, unavailable context, Customize/reset, retained manual edits, reviewed installation and in-flight selection guards.
- [x] Implement automatic selection and a concise recommended-selection summary in `ModuleSetupPanel.vue`; reuse existing collapsible components, grouped checklist and translations.
- [x] Verify frontend tests/build, focused and full backend regressions, contract drift and CI strict typing for all platforms. Run desktop tests if backend startup/layout changes.
- [x] Review final boundaries and diff; restart the idle app and verify contextual automatic setup through the browser without starting installation.

No new dependencies, commits or pushes. Preserve all earlier uncommitted work.

## Review and live verification

Independent review found no important defects in recommendation ordering, schema generation, context overrides, refresh behavior, request guards, accessibility or the reviewed installation boundary.

The live backend suggested the supported ACE-Step starter. Following the Stable Audio training setup link instead selected only Stable Audio 3 automatically, with Customize closed. The real requirements review expanded it to Media tools plus Stable Audio 3. Verification stopped before Start setup; no install job or download was created. The app was restarted only after queue/setup/engine accounting was idle, and the automatic-selection step is left open for review. Screenshot: `/tmp/openfabric-automatic-setup.png`.

All 165 frontend files / 1,544 tests and the strict TypeScript production build passed. The 64 focused backend catalog/routes/jobs/install/support tests passed. All 21 CI strict-mypy commands across Linux/macOS/Windows typing paths and the generated-contract drift check passed. The final full backend suite ran 2,031 tests successfully with 10 skips, using isolated configuration/library/engine paths. Desktop startup and packaging were unchanged; desktop tests were not repeated. No model inference or hardware-capacity benchmark was performed.
