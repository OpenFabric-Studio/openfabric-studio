# Local security, storage and retention

This is an implementation inventory, not a certification of erasure, encryption, legal permission or model quality. It was reviewed on 2026-10-14 against source revision `dee6721` and the artist-production changes in this working tree. No user library, private configuration, cloud account or real training job was used for the review.

## Network boundary

The source launcher, Vite development launcher and Electron backend bind to `127.0.0.1`. The managed ACE-Step API does too; YuE defaults to loopback but its host is configurable. GPT-SoVITS is an independently operated service with a configurable API URL. Changing these settings can change where data goes. A configured service is not evidence of a private or healthy service.

The main application has no built-in user authentication. Its middleware requires a loopback client peer and loopback Host on every HTTP request, and checks Origin/Fetch Metadata on mutations before reading upload bodies. Explicit development origins are supported. Headerless native loopback clients are intentionally permitted. These checks constrain browser abuse; they do not authenticate local processes or authorize remote access through a local proxy. Keep the application and local engine ports on loopback. See [the security policy](../../SECURITY.md), [local-access middleware](../../backend/app/module_security.py), [source launcher](../../desktop/scripts/launch-source.js) and [desktop server](../../desktop/src/server.js).

“Local” is a workflow choice, not a promise that the entire app is offline. Optional OpenRouter actions send approved text/prompts and references to OpenRouter and the selected provider. Optional temporary clip sharing exposes an approved silent video through a separate clip-only Cloudflare tunnel; anyone holding its link can fetch it during its sharing period. It does not expose the main UI, but it is still remote disclosure. Downloads/installers can contact package and model hosts. This review did not enable any of those actions. See [OpenRouter privacy and recovery](../openrouter-media.md) and [temporary clip sharing](../video-studio.md#character-swap-on-a-provided-video).

## Storage inventory

The active library is resolved by backend configuration. The source default is `backend/data`; `OPENFABRIC_DATA_DIR` supplies another default, while a saved folder selection overrides it after restart. The configuration pointer is outside the library at `~/.openfabric-studio/config.json`, or `OPENFABRIC_CONFIG`. Do not publish either private configuration or a copied library.

| Location relative to active library | What it can contain | Retention implication |
| --- | --- | --- |
| `voices/<voice-id>/recordings/` | Original singing uploads | Excluding a source from preparation does not remove its original. |
| `voices/<voice-id>/preparations/` and `preparation.json` | Normalized/separated/cleaned vocals, dry samples, reference clips, selections, original source/time measurements | Re-preparing can leave older generations. These are sensitive source derivatives. |
| `voices/<voice-id>/builds/`, legacy `runs/`, `artifacts.json`, `voice.json` | Reviewed build snapshots, assembled audio/dataset clips, reference-only and trained models, full training state, identity/settings/progress | A published model can encode the singer; treat it as sensitive material alongside recordings. |
| `voices/<voice-id>/trials/` | Held-out comparison uploads, outputs, ratings and job metadata | Held-out does not mean automatically deleted. |
| `voices/_apply/` | Track conversion receipts/progress, worker receipts and temporary source/reference/converted WAVs | Lives outside the individual voice folder; associated accessible work copies remain pending separate review. |
| `files/` | Imported originals, generated music, stems/MIDI, replacements | Voice deletion does not delete these tracks. |
| `files/_versions/` and `files/_exports/` | Immutable original/converted versions, encoded exports, diagnostics and provenance sidecars | These can remain usable without the original voice model. Deleting a track is a separate action. |
| `files/artist-production/sources/` and `renders/` | Immutable guide/accompaniment sources, guide previews, converted vocals and independently mixed masters | Linked graph assets are inventoried as retained/pending; voice-folder deletion does not remove them. |
| `models/seed-vc/` | Shared downloaded Seed-VC weights/cache | Shared engine weights are retained; they are not a singer-specific trained model. |
| `aicollector.db` and SQLite sidecars | Track/project/job/history/version catalog; artist/permission and deletion audit records when integrated | Deleting a voice is not database sanitization. Audit receipts intentionally persist. |
| `voice-profiles/`, `speech-clone-trials/`, `audiobooks/` | Speech references, consent/transcripts, trials/reference snapshots, book sources/drafts, chapter audio and review media | Speech-profile deletion does not erase every retained snapshot, chapter or trial. |
| `videos/`, `video_projects/`, `video_characters/`, `video_character_training/`, `video_character_trainer.json` | Pictures/videos, audio attachments, character inputs, local adapters and trainer settings | Separate workflows and deletion ownership. |
| `providers/` | Provider settings/catalogs, quoted/submitted job receipts, retained cloud media and temporary clip-sharing copies | Local removal does not establish provider removal. Some billing receipts deliberately survive media deletion. |
| `logs/` | Process and backend diagnostics | Can contain filenames, paths and engine output. Support-report allowlisting is not log sanitization. |

The library move inventory is defined in [data_root.py](../../backend/app/data_root.py). It is not an inventory of every external cache or backup. Desktop defaults to an `OpenFabricStudio` root under the platform's per-user application-data directory, with `data/`, `logs/`, `engines/`, `tools/`, `cache/` and `state.json`; Electron's remembered root/port configuration lives separately in its `userData` directory. See [desktop paths](../../desktop/src/paths.js).

`OPENFABRIC_LOG_DIR` can put logs outside the library. Seed-VC can leave an engine `checkpoints` link to library weights; an existing real checkpoint directory is preserved. ACE-Step datasets and weights can live inside its checkout. Source installs can also use normal Hugging Face/Torch/package caches and operating-system temporary directories. Desktop configures its own cache roots. Browser storage retains preferences, selected voice IDs, legacy presets and conversion display metadata; browser downloads and backups are additional copies. None is covered by an individual voice-folder delete.

## Reviewed singing-voice deletion

The previous voice delete handler used `shutil.rmtree(..., ignore_errors=True)` and reported success. That was insufficient evidence of deletion. The [retention implementation](../../backend/app/voice_retention.py) replaces that model with:

1. A durable plan recorded in the catalog before inventory. Planning freezes new mutations/use of that voice when the integration guards are installed.
2. An inventory with root aliases and relative filenames/stable IDs, never public absolute paths. Owned files are content-hash bound to the review. Shared weights and derived tracks/versions/exports are retained; logs and unmanaged backups/downloads remain explicitly unresolved/external.
3. Explicit confirmation bound to the voice ID and reviewed plan digest. Execution drains owned comparison/preparation/build/conversion work before touching files. Changed inventory requires another review.
4. Individual owned-file deletion and empty-directory cleanup. Symlinks, inaccessible paths and deletion failures remain pending. Partial deletion keeps voice metadata visible for review and retry. The operation does not follow a redirected voice root or erase converted tracks/shared weights.
5. Persisted receipts describing completed, retained, pending and external items. Interrupted execution stays frozen; reconciliation observes accessible absence and does not silently resume destructive work. An explicit retry is required.

A `deleted` status means the reviewed voice folder was observed absent at receipt time. It does not mean every copy of the singer or converted audio was erased. `full_erasure_verified` remains false. Receipts use SHA-256 and a previous-receipt digest to detect inconsistent stored claims; they are **unsigned**, and anyone able to rewrite the full local catalog can also recompute those hashes. They prove neither signer identity nor permission. Keep a reviewed receipt outside the catalog if an independent comparison record is needed.

Retrieval verifies every stored receipt predecessor. A history is limited to 4,096
receipts, and a new execution reserves a slot for its terminal receipt before
draining workers or deleting files. Capacity exhaustion leaves files, plan and
history unchanged. Preserve the complete history and review catalog recovery;
do not delete older receipts to bypass the guard.

Creating a plan immediately freezes the voice. Closing its review UI does not withdraw the durable plan. Do not describe dismissal as “cancel deletion” unless an explicit, unexecuted-plan withdrawal is implemented.

Original uploads, derivatives and library databases are ordinary filesystem/SQLite data. No application-level audio/model/database encryption or secure-erasure guarantee was found in the reviewed paths. File deletion and row deletion do not promise removal from filesystem snapshots, SSD remapping, SQLite free pages/WAL, backups, browser downloads, provider retention or other people's copies. Operating-system or volume encryption protects stored data according to the operator's configuration; the app does not verify that it is enabled and it does not protect data from an already authorized local process.

## Operator and implementation requirements

Use a dedicated OS account/library with appropriate filesystem permissions and an encrypted local volume when recordings require protection at rest. Confirm the actual library, external log directory, engine caches and backup destinations. Keep sensitive folders out of automatic sharing/sync unless that disclosure is intended. Review and retain permission evidence before ingesting recordings; the old “intended singer” checkbox is source-identity confirmation, not consent to clone, train, publish or transfer.

Keep authorization on the backend at upload, preparation, training/resume, comparison, conversion/retry and export boundaries. Recheck live scope/revocation after awaited work before publication. A usable checkpoint, cached browser selector or historic grant is not current authorization. Legacy/unknown permission must remain blocked until reviewed. Do not infer provenance from titles or a completed job alone.

For a complete retention feature, retain the freeze across all mutation routes and restart recovery; make failures actionable; provide separate reviewed decisions for derived outputs and external copies; inventory logs/backups without silently scanning private destinations; reject symlink redirects at every serving/writing/deletion root. Revocation, deletion of a model, deletion of a track and provider deletion are distinct operations. UI copy must identify which one is being performed.

The source review and temporary-library regressions verify local browser access checks, hash-bound review, owned-file deletion, explicit retained copies, interruption/cancellation, root/symlink rejection and receipt hashing. They do not establish secure erasure, external/provider cleanup, real GPU behavior, OS encryption status or deployment security beyond the examined launch configuration. Route/UI integration and full suites must be verified separately before release.

## Privacy-minimal operating policy

The application does not implement automatic retention schedules, encrypted backups,
or a data-subject-request service. Agree retention periods and exceptions before
ingestion, then assign an operator to review them. Use stable artist/session IDs and
short recording labels; keep signed agreements and unnecessary personal/account
details in a separately protected evidence store. A local evidence reference is
preferable to embedding an agreement or home directory in a description.

Keep originals, held-out audio, model revisions and production evidence only for
their agreed purposes and periods. Review recordings, unused preparations,
processing work copies, failed outputs, logs and browser downloads separately.
Released masters and accounting/audit records can have different contractual or
statutory retention requirements; record conflicts and obtain appropriate review
instead of treating a singer-folder deletion as permission to remove everything.

Configure encrypted, access-controlled backups outside Git, with reviewed retention
and restoration procedures. Keep a private inventory of backup destinations,
snapshots and any separately approved transfers. Restoring a backup must preserve
revocations and deletion freezes; restoring old permissions is not renewed consent.
The app neither enumerates arbitrary backup folders nor proves their expiration.

No cloud telemetry of voice assets was added. The strict production subprocesses
use the existing local engine with offline model-host settings; this is not a
system-wide network sandbox. Existing optional cloud and installation workflows
remain separate. Require explicit, separately recorded approval for each private
recording transfer and its recipient/purpose; the local app has no account-based
approval enforcement. Backend/engine logs can still contain paths. Restrict them,
review and redact support material before sharing, and do not claim that existing
logs are centrally sanitized.

For an access, correction, restriction or deletion request, verify the requester
outside this unauthenticated application, freeze new use where appropriate, export
only the requested scoped evidence, and reconcile accessible copies and backup
exceptions. Maintain a private request/decision record and avoid sharing raw
recordings or checkpoints with accountants. Applicable GDPR roles, legal bases,
retention duties, voice/personality rights and contractual exceptions need legal
review; these operating steps are not a claim of built-in legal compliance.
