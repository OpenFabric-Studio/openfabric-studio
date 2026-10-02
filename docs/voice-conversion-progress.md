# Voice conversion status

When you add a cloned voice to a track, its status appears alongside the audio versions. You can keep listening to Original or another completed version while the new voice is processed.

The stages are waiting for shared processing resources, separating vocals, preparing audio, loading the voice model, analyzing the source voice, converting audio chunks, and checking/mixing the result. Waiting explains which operation currently holds the shared accelerator. Training and multiple conversions still run sequentially; a waiting job has not started converting your song.

Elapsed time includes waiting. Completed, failed and cancelled jobs retain their final elapsed time after a refresh. Retrying starts a new attempt with a new clock. If the app crashes, an interrupted job freezes at its last saved observation: this is its last-known elapsed time, because the exact crash time is unavailable.

Conversion progress counts completed audio chunks. The estimated remaining time refers to the **current stage**, not the entire job. It becomes available after multiple comparable measurements and excludes the initial warm-up. Loading, unmeasured stages, waiting and stale observations show no numeric estimate. Long or difficult inputs can change the measured rate.

The display does not reduce conversion quality or change model settings. Older jobs created before this feature may lack timing metadata and show only their available status.

## Manual verification

1. Add a voice version to a track and leave Original selected. Confirm the new version's status remains visible and the playing audio stays Original.
2. Add another voice version while the first is converting, or add one while voice training is active. Confirm the waiting job names the operation holding the accelerator and its elapsed time continues.
3. Observe loading, analysis and conversion. During conversion, verify chunk counts increase and the stage estimate appears only after enough measurements.
4. Refresh the page during conversion and after completion. The active clock should continue from its saved start; the finished clock should remain fixed.
5. Cancel a waiting job and an active job. Confirm they stop cleanly, retain their final elapsed time and release resources for other work. Retry and confirm a fresh clock and progress count.

Automated tests cover mocked model execution, contracts and UI behavior. A real GPU conversion remains necessary to verify runtime performance on a particular machine.
