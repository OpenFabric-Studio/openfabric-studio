# Quality review, recoverable setup and export provenance

These features extend the existing local job registries. Original audio, accepted speech takes and configured model installations are retained. Audio analysis, encoding and speaker screening survive browser navigation; cancellation drains their owned workers.

## Setup and downloads

Open Settings → Modules to review a feature's installed dependencies and installation plan. License cards distinguish integration code, published weights, auxiliary checkpoints, tools and source recordings. A linked declaration does not certify the identity or terms of an arbitrary local replacement file. Unknown terms remain visible.

Owned pinned artifact downloads resume partial files, retry transient network failures with bounded backoff and detect stalled transfers. Progress shows bytes, attempt and verification state. Final size and SHA-256 must match before the completed file replaces its destination. Permanent HTTP/integrity failures require review. Third-party installers retain their own retry behavior; the app does not replay arbitrary installation commands or paid generation requests.

The native music model downloader receives a separate reliability patch layered on the earlier resume patch, so already-managed installations can be upgraded without pretending the old patch is absent.

## Optional local speaker screening

In an audiobook's passage review, select a saved accepted passage or completed audition for the current voice. Listen to the reference, explicitly approve it and enter a threshold calibrated with representative examples. The optional CPU encoder compares the saved audio with that reference; it does not call a speech provider, regenerate a line, accept a take or identify a person.

The score is cosine similarity, not a probability. Short/silent material can be unsuitable. Activity screening is based on amplitude, not proof that every analyzed sample contains speech. Long recordings use a bounded analysis span. Review a flagged line by listening and use the existing repair workflow if needed.

Results retain encoder family, checkpoint hash, preprocessing and package versions, reference/target audio hashes, accepted take/revision, render snapshot and cast/profile identity. Known replacements and assignment changes invalidate results. Embeddings are not compared across different encoders merely because their dimensions match.

The GPT-SoVITS API does not attest its loaded checkpoint. Such saved-audio comparisons show an unverified-renderer warning; external checkpoint changes cannot be inferred from a server URL or voice name. Select the intended reference again and re-run screening after external engine changes. This limitation does not change the measured similarity between the captured audio files.

The encoder is an optional dedicated environment using [SpeechBrain ECAPA VoxCeleb](https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb). Base installation does not install Torch or download its weights. Settings exposes the dependency setup and explicit local weight configuration. The runner uses a fixed inspected architecture and a pinned checkpoint hash, without remote YAML execution or implicit model fetching.

## Audio analysis and loudness targets

Use an audio version's analysis/export controls to measure integrated loudness (LUFS), loudness range, sample peak (dBFS), oversampled true peak (dBTP) and full-scale sample evidence. A sample peak below 0 dBFS does not exclude intersample overload. Silence has no finite integrated loudness; an unmeasurable target is shown as inconclusive.

Normalization is off by default. Optional house presets are music (−14 LUFS / −1 dBTP), spoken audio (−16 / −2), and an EBU loudness target (−23 / −1), plus bounded custom targets. These are explicit processing choices, not guarantees that a particular distribution platform accepts the output. Two-pass FFmpeg loudness processing retains the source and records whether linear gain or dynamic limiting was used. The final encoded output is measured again; target assessment uses ±0.5 LUFS and a 0.1 dB true-peak tolerance, with a warning outside those bounds. Export records retain the requested profile and measured assessment through reload and recovery. Missing processed-file measurements stay unavailable; they are not replaced with measurements of the dry source.

Track versions/editor downloads and video exports expose these processing choices. Canonical audiobook chapters and speech trials stay dry; their new manifests document accepted-source provenance without silently applying the global track settings.

Preview/download use the same completed encoded file. Increasing bit depth or sample rate does not recover detail absent from the source.

## Export records and labels

Completed exports have adjacent hash-bound JSON and readable manifests. Supported media metadata contains a short origin/export record while preserving source/provider tags and watermarks. Provenance describes known app workflow facts and transformations; it is not a digital signature or proof of copyright ownership.

Origin categories are generated, mixed, recorded and unknown. Imported material remains unknown unless the user explicitly declares a recording; that declaration cannot turn a known generated source into a real recording. Conditioning photos are recorded as inputs and do not become generated images merely because a video model used them. A visible AI label is an explicit video-export option when generated components are known.

Disclosure and machine-readable marking obligations depend on the content, actor and applicable terms. See the official [Article 50 text](https://ai-act-service-desk.ec.europa.eu/en/ai-act/article-50) and [Commission guidance](https://digital-strategy.ec.europa.eu/en/faqs/transparency-obligations-under-article-50-ai-act). The manifest feature does not claim legal compliance or a robust embedded watermark.

## LTX experiments

The app's production engine remains pinned at 0.15.12. The isolated benchmark supports an exact-source candidate with the merged image-preprocessing and unfused-LoRA changes, explicit resident/low-RAM modes, fixed seeds/settings and optional captured references/adapters. Unfused mode is rejected under low RAM; upstream still fuses the distilled stage-two adapter.

See [compatibility and benchmark evidence](ltx-compatibility-and-benchmark.md). Basic inference and synthetic adapter/parity tests do not establish character likeness, lip synchronization or a universal memory/speed benefit. New scoring models and generated training datasets remain deferred until their licenses and held-out quality evidence are reviewed.
