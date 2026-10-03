# Ebook import and module setup

Open **Audiobook → New audiobook**. Imports accept pasted text, TXT, EPUB, and unencrypted MOBI up to 50 MiB. TXT and EPUB are read in OpenFabric. MOBI still needs [Calibre](https://calibre-ebook.com/download); **Settings → Setup & modules → Ebooks** checks `ebook-convert` and links to the installer. On macOS the app discovers `/Applications/calibre.app/Contents/MacOS/ebook-convert`. Custom installations can set `OPENFABRIC_EBOOK_CONVERT`. Copy Calibre out of the disk image before using its command-line tools.

Imports save the original file and an editable chapter draft. The editor changes only after **Use imported chapters**. Review chapter boundaries and exclude unwanted front matter, contents and licensing pages. Choose a saved Speech narrator, preview a short excerpt, then create the audiobook. Imports do not bypass encryption. Automatic chapter detection still needs review.

**Save reviewed draft** persists edits independently of the browser. Narration saves completed short sections and chapter audio to the library. Pause and cancel take effect after the current upstream section; resume reuses completed sections. After backend interruption, unfinished books are paused for explicit resumption. Failed export retries reuse completed chapter audio. The final download is PCM WAV, plus MP3 and an M4B with chapter markers, title, author, and an optional cover when ffmpeg has those codecs; chapter outputs must have matching format and sample rate, and RIFF size limits apply. Mock speech is labelled silent audio.

The original MOBI can be downloaded. Deleting an import needs separate confirmation and is refused while an audiobook references it. Interrupted deletion restores its source if the draft transaction was not committed; ambiguous recovery preserves files and reports an error. Library migration includes speech profiles, trials, audiobook databases, drafts and audio, with SQLite backups and rollback.

## Settings and header

**Settings → Setup & modules** checks the computer, selects features and dependencies, reviews requirements, then installs and verifies. Model downloads are off by default. Plans show publisher artifact sizes where known. Python dependencies and model weights can have unknown sizes; the disk admission floor is a policy, not a prediction or guarantee of sufficient space.

Setup uses pinned sources and verified artifacts in a writable managed home. Configured external checkouts are preserved and can be explicitly checked without reinstalling them. Jobs persist through navigation and have cancellation/recovery controls. Changed approval scopes require a fresh review. Stop running music engines and finish jobs before setup; setup blocks incompatible new work until its workers drain.

The six compact header indicators cover ACE-Step, YuE, Speech, Singing, Video and Media tools. Keyboard/touch tooltips open Settings details. Directories, responding ports and interpreter filenames alone do not mean ready. Tool versions, environment receipts, service identity and loaded models provide separate evidence. Speech also requires a validated real result for its current checkout/environment. Failed polling displays unavailable status.

Calibre, whisper.cpp/model configuration, and GPT-SoVITS pretrained weights/API startup have explicit manual steps. Native Linux YuE needs a source build. LTX generated scenes require Apple Silicon macOS; cover/visualizer modes have lighter dependencies. See [platform setup](platform-setup.md) for launchers and desktop support boundaries.

## Verification boundaries

Real MOBI conversion was checked on macOS ARM64 with official Calibre 9.15.0 and the [Project Gutenberg Alice MOBI](https://www.gutenberg.org/ebooks/11). The final extraction produced 17 reviewable chapters and 163,259 characters with the source preserved. The download digest matched the publisher release. Calibre, fixtures and verification libraries remained outside the repository and the user's library.

A short, non-mock speech request through the existing local GPT-SoVITS CPU service produced validated 4.5-second mono PCM audio at 32 kHz. Its profile and output used a temporary library. These checks establish conversion and short synthesis; they do not measure narration quality or establish full-book performance.

A clean Python 3.12 environment installed the hashed base wheels, passed dependency checks and imported the lightweight backend packages on macOS ARM64. CI is configured to check that lock on Windows, macOS and Linux. Unit tests and strict typing exercise temporary libraries, platform paths, process ownership, origin restrictions, revision conflicts and recovery without downloading GPU models.

Browser checks exercised the wizard and real MOBI upload/review against an isolated backend. The header measured 44 px and the page had no horizontal overflow at 320 px. Automated WCAG A/AA checks on the imported-book screen passed after contrast fixes. Actual clean Windows/Linux engine installations and GPU inference remain unverified.

Final local checks on 2026-10-03: 881 frontend tests passed; the strict frontend build passed; backend discovery ran 788 tests with 778 passing and 10 platform-dependent skips; 76 desktop tests passed. Generated contracts matched, the complete checked Python scope passed strict typing for Linux/macOS/Windows paths, and the CI workflow parsed successfully. The new cross-platform CI installation matrix is configured but has not run remotely.
