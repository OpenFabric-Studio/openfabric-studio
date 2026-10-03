# Approved ebook and setup design

Approved by Seth with “do it” on 3 October 2026, following the audit and interactive mockup.

## Ebook creation

Accept unencrypted MOBI, normalize with the optional Calibre CLI, and extract plain text in EPUB reading order. Show editable title and chapter text, chapter inclusion and extraction warnings before narration. Preserve the original source and drafts. Bound uploads, archives, text and subprocess work; reject unsafe documents and never truncate silently. Missing Calibre has an actionable setup link. Narration uses existing consent-backed profiles and short previews, stores completed sections, serializes the shared speech engine, recovers interrupted jobs and supports pause/resume/cancel. Existing WAV export remains the output format.

## Setup and modules

Settings contains one feature-based wizard: computer check, feature selection, review requirements, install and verify. A typed backend inventory distinguishes unsupported, missing, partial, installed and ready states. It reports evidence and specific actions rather than assuming a folder or HTTP response means a working model. Installations are catalog-controlled, persisted, cancellable and recoverable. Existing external installations are preserved. No arbitrary command, URL or destination is accepted from the browser. Heavy downloads are selected explicitly. Privileged and third-party manual steps remain visible.

## Header and platform boundaries

Keep the compact header and expose ACE-Step, YuE, Speech, Singing, Video and Tools indicators with accessible tooltips and Settings links. Failed/stale polling loses current-ready status. Generated video remains Apple Silicon-only; CPU singing training is unsupported. Desktop starts after minimal backend bootstrap; optional engines use the selected writable engine root. Linux source setup and unsupported desktop platforms have honest instructions. Clean Windows/Linux installation and GPU inference require platform evidence beyond tests on this Mac.

## Storage and verification

Library migration must include audiobook, speech-profile and speech-trial databases/media, rewriting only stored paths with SQLite backups and rollback. Never migrate the active user's library during development. All tests use temporary config, data and engine roots. Complete frontend, backend, contracts, strict Python platform checks and affected desktop checks, then inspect the integrated diff. Do not commit or push this task without a new user instruction.
