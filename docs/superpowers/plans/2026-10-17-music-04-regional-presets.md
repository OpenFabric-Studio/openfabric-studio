# Regional Instruments and Presets Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans; independent asset/theory reviews may use focused agents under AGENTS.md. Checkboxes are future work. No downloads, commits or published packs are authorized by this plan alone.

**Goal:** Add the report's 24 regional presets, supported by traceable instrument assets, appropriate rhythm/melody rules and honest review status.

**Architecture:** One Beat Maker from plan 03 consumes versioned preset data and instrument manifests. A preset compiles to the same validated BeatDocument as a hand-edited pattern. Authentic sample playback and microtuning belong to that renderer; there is no separate regional sequencer or hidden model download.

**Tech Stack:** Existing Vue workspace, proposed Pydantic preset contracts, JSON preset/asset catalogs, deterministic Python pattern compiler, NumPy sampler from plan 03, existing module setup for optional sound packs.

---

## Dependencies and boundaries

Report sections 6.1–6.9, sample choices in 5.4, quality in 8, rights in 9. Plan 00 supplies evidence/asset admission; plan 03 supplies pattern/render/export; plan 01 supplies tuning/measurement support. Begin sample-rights and musician review work in parallel with earlier phases, because code cannot manufacture either.

The report's scale spellings, cycle transcriptions and recognition percentages are proposals. They are not accepted ethnomusicological definitions or measured quality results. Record variants and sources rather than freezing one version as universal.

### Proposed files

| Responsibility | Create or modify |
| --- | --- |
| Preset schema/catalog | New `backend/app/beat_preset_contracts.py`, `beat_presets.py`, `backend/app/data/beat_presets.json` |
| Scale/tuning/rhythm catalog | New `backend/app/data/beat_scales.json`, `beat_rhythms.json`, `beat_tunings.json` |
| Articulations | New `backend/app/beat_articulations.py`; extend proposed `beat_render.py` and `beat_contracts.py` from plan 03 only where required |
| Instrument pack manifests | Extend the single proposed `backend/app/assets/beat-kits.json` catalog from plan 03; use exact pack manifests/hashes and attributions established in plans 00/03 |
| Assets/setup | Extend existing `backend/app/module_contracts.py`, `module_catalog.py`, `module_install.py`, `module_security.py` if a separately installed pack is introduced; register exact artifacts in the existing manifest mechanism |
| Browser | New `frontend/src/views/music/BeatPresetBrowser.vue`, `BeatPresetDetails.vue`, `frontend/src/locales/beatPresets.ts`; extend proposed Beat Maker page/API from plan 03 |
| Tests | New `backend/app/beat_presets_test.py`, `beat_articulations_test.py`, `beat_preset_assets_test.py`; frontend component tests beside new components |
| Evidence | New `docs/music-presets/README.md`, per-preset review records, pack attribution files and dataset-source inventory outside Git for private documents |

These paths are proposed. Reconcile names with plan 03 before implementation; do not create duplicate catalogs.

## R01 — Define the preset contract and catalog states

- [ ] Write schema tests for a complete preset, missing instrument IDs, invalid degrees, nonfinite tuning, unrelated region labels, unsupported meter and unbounded ornaments.
- [ ] Define `Preset` with stable `id`, catalog `version`, title, region/tradition, category `traditional|fusion|modern_pop`, BPM default/range, meter, scale/tuning IDs, rhythm ID, instrument roles, articulation rules, prompt tags, required pack identities and review record IDs.
- [ ] Store review status as `unreviewed|reviewed|needs_revision` with the reviewed preset/pack hash, reviewer scope and notes. A changed definition invalidates the applicable review; a name in a field does not authenticate the reviewer.
- [ ] Store instrument availability separately from review: `available|pack_missing|license_unresolved|unsupported_articulation`. The UI can show experimental unreviewed presets honestly; it cannot preview nonexistent sounds.
- [ ] Define traditional/fusion/modern as arrangement categories, not a quality rating or a guarantee of authenticity.
- [ ] Expose a bounded catalog through the Beat Maker API; filter/search by region, instrument, category and availability. No arbitrary executable code or URLs in preset JSON.

**Exit:** Every selectable preset resolves to real approved instrument IDs and a valid pattern configuration.

## R02 — Assemble assets with recoverable pack installation

- [ ] Inventory which required instruments have suitable recordings. Record both absence and alternate articulations. Do not assume a General MIDI bank contains erhu, guzheng, gayageum, gamelan or tabla.
- [ ] Recheck exact VCSL files and their CC0 declaration, CdM gamelan files and Artistic-2.0 declaration, any whole-bank SoundFont agreement, and each independently selected Freesound recording. Publisher URLs from the report are starting points, not sufficient per-file clearance.
- [ ] For every file record source URL, author, source revision, file SHA-256, byte length, license text/URL, redistribution rule, sample pitch/tuning, velocity range, articulation and channel/sample-rate facts.
- [ ] Obtain a scoped confirmation where redistribution or derived-model use remains unclear. A music-use statement does not automatically answer redistribution or training questions.
- [ ] Keep whole-bank-only assets as whole banks. Do not extract their samples into a redistributable pack.
- [ ] Prefer a small verified pack over a large unreviewed download. Split optional packs by useful instrument groups with displayed download/disk sizes; reuse already valid artifacts.
- [ ] Use the existing reviewed setup flow, allowed hosts, size/digest checks, staging and atomic promotion. Cancel/resume must not replace a working pack with a partial one.
- [ ] Test archive traversal, symlinks, compressed/decompressed size limits, missing notices, wrong hash, corrupted WAV/SFZ metadata and an interrupted promotion.
- [ ] Add a CI asset audit: a sample without its manifest/license/hash entry fails. Private user samples are not bundled or uploaded by this audit.
- [ ] Include required notices and attributions in source packages, desktop assets and exported provenance. No model/sample cache in Git.

**Exit:** A clean machine can install a reviewed pack; the audit explains every distributed byte's declared source and terms.

## R03 — Scale, tuning and pitch representation

- [ ] Define tonic, reference frequency and ascending scale degrees in cents. Resolve an event's total pitch to MIDI note plus residual cents without silently rounding to 12-TET.
- [ ] Implement scale locking in the generator and editor. Explicitly tagged grace notes/bends may leave the scale; they are not silent validation exceptions.
- [ ] Support plan 03's rational tick grid: PPQ 960; meter numerator/denominator; quarter-note BPM; derived integer subdivisions. Display the dotted-beat equivalent for compound meter to avoid a misleading tempo control.
- [ ] Encode the report's Chinese gong/shang/jue/zhi/yu and heptatonic proposals as sourced candidate definitions. Keep differences between rotations and named traditional functions visible in notes.
- [ ] Encode Japanese yo, in/miyako-bushi, hirajoshi, kumoi, iwato, yonanuki and Ryukyu candidates with named variant notes. Do not merge distinct tunings merely because a Western scale database gives them one label.
- [ ] Encode Korean pyeongjo/gyemyeonjo as explicitly approximate candidates, with ornaments and rhythm definitions separately represented.
- [ ] Implement measured per-pack slendro/pelog tables for gamelan. Do not call a generic equal 240-cent scale the tuning of a real ensemble. A synthetic equal-division experiment must be labeled accordingly.
- [ ] Encode Hindustani thaat candidates, tonic/fifth drone and ascent/descent/phrase constraints. A scale alone must not be labeled a complete raga performance. Carnatic melakarta breadth remains an optional research extension in plan 08.
- [ ] Encode the report's Thai equal-division proposal as provisional until a source/sample-specific tuning is established; Isan/khaen modes remain distinct.
- [ ] Add tests for octave wrap, negative transposition, every generated scale degree, repeated deterministic seeds, frequency ratios and microtonal MIDI export policy.

**Exit:** Notes render in the declared tuning; labels describe the actual source and approximation.

## R04 — Rhythm-cycle library: all 15 report entries

Each row is a development target. Exact strokes, grouping and tempo interpretation require a source/review record before being presented as reviewed.

| Stable report ID | Tradition/arrangement | Required implementation detail |
| --- | --- | --- |
| `jp_taiko_matsuri` | Taiko festival-inspired | Multiple drum roles, don/ka/doko articulation distinctions and bounded fills |
| `jp_lofi_koto` | Japanese lo-fi fusion | Swung boom-bap plus scale-locked koto motif |
| `jp_citypop` | City-pop-inspired | Disco/pop groove, syncopated bass and chord voicings |
| `cn_luogu_opera` | Opera percussion | Leading drum/clapper and reviewed gong/cymbal responses; school-specific source |
| `cn_guofeng_pop` | Guofeng fusion | Pop/trap rhythmic layer with guzheng/dizi motif |
| `cn_lion_dance` | Lion-dance-inspired | Drum/gong/cymbal interplay; an explicit tempo envelope for acceleration, not random speed |
| `kr_samulnori_gutgeori` | Korean percussion | Compound grouping and separate janggu/buk/metal roles |
| `kr_kpop_gugak_fusion` | Gugak/pop fusion | Pop rhythm and 808 with traditional instrument roles |
| `id_lancaran` | Javanese cycle | Reviewed 16-beat cycle, gong ending and differentiated subdivisions |
| `id_bali_kotekan` | Balinese interlocking | Complementary polos/sangsih event streams and measured paired tuning |
| `in_teentaal_fusion` | Hindustani fusion | 16-beat grouping, sam/khali metadata and articulated tabla pattern |
| `in_bhangra` | Punjabi pop/dhol | Reviewed dhol chaal accents and tumbi part |
| `in_bollywood_dance` | Film/pop dance | Drum/dholak interplay and optional string layer |
| `th_ching_cycle` | Thai classical-inspired | Reviewed open/damped cymbal alternation and density levels |
| `th_molam_khaen` | Isan fusion | Khaen drone, phin motif and accompanying rhythm |

- [ ] Record source, meter/grouping, tick length and role-to-articulation map for each row.
- [ ] Add tests for cycle length, first/last accent placement, meter changes, pattern-chain boundaries and no accidental phase reset across repetitions.
- [ ] Add semachi/eotmori and other report-mentioned jangdan as distinct supported definitions only after source verification; meter support must cover 9, 10 and 18 subdivisions without forcing 4/4.
- [ ] Represent tala and gong-cycle markers as metadata displayed with the grid. Do not encode all accents as Western backbeats.
- [ ] Compile any acceleration/density change to explicit event timing; preview/export use the same compiled events.

**Exit:** Every rhythm's duration, grouping and required articulations are inspectable and tested.

## R05 — Motifs, arrangements and articulations

- [ ] Define bounded seeded motif choices with repetition, sequence, response and cadence rules. Variation preserves a recognizable motif instead of regenerating every note independently.
- [ ] Add per-instrument range constraints and playability limits. No out-of-range resampling disguised as an authentic articulation.
- [ ] Implement plucked grace notes/glissandi/tremolo, sustained portamento/vibrato, breath/noise and drum stroke variants only where supported by actual recordings or clearly labeled synthesis.
- [ ] Add bounded pitch-envelope breakpoints for bends. The renderer applies the same envelope to preview and export; MIDI uses the declared per-voice bend-channel policy.
- [ ] Provide tonic/fifth drones, heterophonic variants and complementary interlocking parts where the preset calls for them.
- [ ] Test envelope continuity, no frame discontinuity/click at sample joins, anti-aliasing under transposition, bounded polyphony and deterministic articulation selection.
- [ ] Build conventional pop/fusion arrangements with explicit sections. Do not imply a guzheng plus unrelated tabla automatically represents a traditional Chinese arrangement.
- [ ] Test that locked instruments, notes, density and region selection stay locked during variation/humanise/fill.

**Exit:** The instrument behavior, not just its label, supports the intended preset.

## R06 — Implement all 24 preset definitions

The following table is the exact report inventory. Scale/rhythm assignments remain sourced proposals until reviewed. No row is counted as implemented merely because its JSON exists.

| # | Preset | Category | Core assets | Theory/arrangement target |
| --- | --- | --- | --- | --- |
| 1 | Guzheng Dawn | Traditional | Guzheng, dizi, xiao | Gong candidate; slow/free phrasing |
| 2 | Erhu Elegy | Traditional | Erhu, yangqin, pipa | Yu candidate; expressive sustained phrasing |
| 3 | Peking Opera Drums | Traditional | Bangu, gongs, cymbals, jinghu | Reviewed opera percussion pattern |
| 4 | Guofeng Trap | Fusion | Guzheng, dizi, 808, hats | Yu/gong candidate; half-time trap |
| 5 | Lion Dance | Traditional | Large drum, gong, cymbals | Reviewed accents and acceleration |
| 6 | C-pop Ballad | Modern pop | Piano, strings, erhu | Pop harmony with pentatonic hook |
| 7 | Kyoto Koto | Traditional | Koto, shakuhachi | Named in/hirajoshi variant; sparse phrasing |
| 8 | Matsuri Taiko | Traditional | Taiko roles, shinobue | Festival-inspired cycle, yo candidate |
| 9 | Lo-fi Kyoto | Fusion | Koto, electric piano, drums | In candidate, swung boom-bap |
| 10 | Tokyo City Pop | Modern pop | Bass, electric piano, brass | Major/yonanuki hook and extended chords |
| 11 | Okinawa Breeze | Fusion | Sanshin, sanba, percussion | Ryukyu candidate and island-folk feel |
| 12 | Shamisen Rock | Fusion | Shamisen, rock drums | Named yo/in candidate and rock arrangement |
| 13 | Gayageum Sanjo | Traditional | Gayageum, janggu | Reviewed gyemyeonjo/ornaments and cycle progression |
| 14 | Samulnori Fire | Traditional | Kkwaenggwari, jing, janggu, buk | Reviewed gutgeori grouping |
| 15 | K-Gugak Pop | Modern pop | Gayageum, haegeum, 808, synth | Pyeongjo candidate and pop sections |
| 16 | Javanese Lancaran | Traditional | Saron, bonang, kenong, gong, kendhang | Measured slendro pack and reviewed cycle |
| 17 | Pelog Night | Traditional | Gender, gambang, suling, gong | Measured pelog/selected pathet and ketawang candidate |
| 18 | Bali Kotekan | Traditional | Gangsa pairs, kendang | Source-specific tuning and interlocking |
| 19 | Gamelan Electronica | Fusion | Verified gamelan pack, kick, bass | Declared tuning with electronic arrangement |
| 20 | Raga Dusk | Traditional | Sitar, tanpura, tabla | Reviewed Yaman/Kalyan phrases and teentaal |
| 21 | Bhangra Rush | Modern pop | Dhol, tumbi, bass | Reviewed chaal and melodic constraints |
| 22 | Bollywood Groove | Modern pop | Dholak, strings, sarangi | Film/pop arrangement; named Kafi/Bhairavi-inspired hook |
| 23 | Siam Court | Traditional | Ranat, khong wong, pi, ching | Verified tuning and piphat-inspired texture |
| 24 | Isan Mor Lam | Fusion | Khaen, phin, drums | Source-specific mode, drone and motif |

- [ ] Build one asset-complete preset through generation/render/export before adding the remaining rows.
- [ ] For each subsequent row: add its schema fixture, confirm packs/ranges/articulations, compile deterministic seeds, render stems, verify timing/tuning and attach source/review status.
- [ ] Keep free-rhythm presets honest: generate explicit expressive events inside a defined render span rather than claiming the step grid captures all free-time performance.
- [ ] Store intended AI prompt tags in preset metadata; generated text identifies inspiration without claiming performer identity.
- [ ] Count and audit exactly 24 stable IDs. Missing assets produce a specific missing-pack reason, not an oscillator substitution under the original instrument name.

**Exit:** Every available preset produces real audio with a complete manifest; unavailable rows explain their concrete dependency.

## R07 — Preset browser and handoff

- [ ] Add region/category/availability filters, search, keyboard-accessible preview buttons and a details card explaining instruments, rhythm, scale/tuning and review status.
- [ ] `Instant beat` compiles the chosen preset with displayed seed/BPM and renders through the existing Beat Maker service. Time the full first-use and warm-use interaction separately.
- [ ] `Open in Beat Maker` preserves the preset/version and makes pattern edits explicit. `Make a song` hands a saved rendered beat to plan 05.
- [ ] Show render/progress state near the top; leave the current beat intact after failed preview or missing pack.
- [ ] Include human-readable sound-pack sizes and a setup link for missing assets. Install/reinstall behavior uses existing installed-state semantics.
- [ ] Test filter counts, missing/unreviewed status, stale preview responses, changing presets mid-render, cancelled rendering and reload of the saved beat.
- [ ] Add translations using the existing locale composition; preserve proper instrument/tradition names.

**Exit:** A user can hear, understand and edit a preset without deciphering engine names.

## R08 — Technical and listening evaluation

- [ ] Render 24 presets × 5 fixed seeds where assets are available; retain exact versions, manifests and audio hashes. Record missing assets as missing, not passing cases.
- [ ] Assert generated-note scale conformance, excluding explicitly tagged ornaments. Test declared frequencies with isolated sustained tones and source-aware tolerance.
- [ ] Test sample/renderer tuning within the report's proposed ±10-cent target where pitch tracking is reliable; noisy/unpitched sources produce inconclusive, not fictitious measurements.
- [ ] Measure render latency on M4 Max for short previews and full arrangements. The report's under-two-second instant preview target is a performance gate to measure, not a promise based on CPU-only design.
- [ ] Arrange review by a practising musician for each tradition; record scope, versions, feedback and required changes. Do not invent reviewer approval.
- [ ] Run the report's proposed 5-second blind identification study: separate traditional/fusion results, sample size and listener backgrounds. Report uncertainty, not a universal accuracy claim.
- [ ] Reject or relabel presets flagged as misleading/caricatured; rerun applicable review after changes.
- [ ] Run asset audit, targeted tests, frontend tests/build and required backend/type checks from plan 09.

**Verification status:** Plan only. Instrument availability, redistributable pack completeness, musician review and recognizability have not been established.
