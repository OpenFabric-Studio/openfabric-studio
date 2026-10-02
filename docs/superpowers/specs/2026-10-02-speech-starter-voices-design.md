# Speech starter voices

The Speech workspace will offer four bundled English VCTK 0.92 reference recordings. Users can preview each recording, inspect its exact transcript, see its source and license, and add it to their own speech profile library. These profiles also appear in the existing Audiobooks voice selector.

The catalog remains separate from the user's library. Adding a voice copies its audio into the configured library and stores the transcript in the existing notes field used by GPT-SoVITS. A nullable, unique starter voice identifier preserves provenance and makes repeated/concurrent imports idempotent. Existing profiles and edited imports must remain unchanged. Deleting an import must never delete bundled audio; importing it again creates a new library copy.

Backend contracts own catalog metadata and client types. Audio endpoints select files using catalog/profile/trial identifiers and reject missing files, traversal, and symlink escapes. The catalog must work offline and without an installed speech engine. Imports validate the bundled audio hash before creating a profile; errors return stable public codes.

The UI offers source previews and translated, accessible controls. Only one player may play at a time; hidden or unmounted workspaces stop playback. Import failure is recoverable, duplicate clicks cannot create duplicate requests, and a late response cannot replace a newer profile selection or creation draft. Licensed references are labeled accurately rather than implying personal consent was collected by OpenFabric.

Speech trial audio, already saved by the backend, will be available for playback and download through an identifier-based endpoint. Mock output remains explicitly labeled as a silent placeholder. No engine models are downloaded and no synthesis quality claim is made without real engine evaluation.

Asset provenance includes the dataset citation, source recording paths and hashes, license, conversion details, exact transcripts, and measured duration/sample rate/peak level. Four representative samples must be independently auditioned before calling the set reviewed. Reference quality does not establish synthesis quality.

Verification covers catalog/media endpoints, recoverable schema upgrade, idempotent/concurrent import, failure cleanup, deletion/reimport, attribution, audio hash/duration/transcript integrity, UI selection/draft/lifecycle behavior, strict generated contracts, and the affected test suites. Tests use temporary libraries and engine paths; no user data or GPU models are touched.
