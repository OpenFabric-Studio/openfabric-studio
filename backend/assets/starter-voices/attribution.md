# English starter speech references

These four dry speech references come from **CSTR VCTK Corpus version 0.92** (2019), published by the Centre for Speech Technology Research, University of Edinburgh. They are examples for reference-based speech synthesis, not trained voice models. The voices keep their anonymous corpus IDs; no real identity, endorsement, or generated-speech quality is implied.

**Attribution:** Christophe Veaux, Junichi Yamagishi, Kirsten MacDonald, *CSTR VCTK Corpus: English Multi-speaker Corpus for CSTR Voice Cloning Toolkit*, Centre for Speech Technology Research (CSTR), University of Edinburgh. Copyright (c) 2019 Junichi Yamagishi.

**License:** [Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/). The corpus license text, including its disclaimer of warranties, is preserved in [LICENSE-CC-BY-4.0.txt](LICENSE-CC-BY-4.0.txt). This audio is separately licensed from the application's MIT source code.

**Primary source:** [University of Edinburgh DataShare, CSTR VCTK Corpus version 0.92](https://datashare.ed.ac.uk/handle/10283/3443). [Official archive](https://datashare.ed.ac.uk/server/api/core/bitstreams/535f4286-e54c-4038-838c-a02285e32cb2/content). Retrieved 2 October 2026.

The source README describes the same studio setup for these recordings: DPA 4035 and Sennheiser MKH 800 microphones, recorded at 96 kHz / 24 bits in a hemi-anechoic chamber, then converted by the corpus authors to 48 kHz / 16 bits and manually end-pointed. These examples use the corpus's `mic2` recordings. The two speakers identified by the corpus authors as having mic2 recording issues, p280 and p315, are not included. [VCTK-README.txt](VCTK-README.txt) is an unchanged copy of the [publisher's README](https://datashare.ed.ac.uk/server/api/core/bitstreams/bb7edd96-5d96-4c0e-8989-1e45597e7b72/content). Accent and region labels come from the archive's `speaker-info.txt`, preserved unchanged in [VCTK-speaker-info.txt](VCTK-speaker-info.txt).

## Included utterances and changes

Each reference is the complete utterance numbered `003`. Original FLAC files were decoded losslessly into mono 48 kHz / 16-bit PCM WAV for browser and speech-engine interoperability. Decoded PCM sample bytes match the original FLAC exactly. OpenFabric Studio did not trim silence, normalize loudness, denoise, resample, add effects, or change any samples. Corpus-supplied endpointing precedes this selection.

The source transcripts are preserved unchanged in `transcripts/`. Their content is also included in `catalog.json` with surrounding whitespace removed. All four utterances say:

> Six spoons of fresh snow peas, five thick slabs of blue cheese, and maybe a snack for her brother Bob.

| Reference | Corpus accent / region | Original audio member | Original transcript member |
| --- | --- | --- | --- |
| VCTK p225 | English / Southern England | `wav48_silence_trimmed/p225/p225_003_mic2.flac` | `txt/p225/p225_003.txt` |
| VCTK p237 | Scottish / Fife | `wav48_silence_trimmed/p237/p237_003_mic2.flac` | `txt/p237/p237_003.txt` |
| VCTK p294 | American / San Francisco | `wav48_silence_trimmed/p294/p294_003_mic2.flac` | `txt/p294/p294_003.txt` |
| VCTK p326 | Australian English / Sydney | `wav48_silence_trimmed/p326/p326_003_mic2.flac` | `txt/p326/p326_003.txt` |

## Measured screening and integrity

Measurements use the complete WAV, including the quiet padding supplied by the corpus. Full scale is 32,768 for signed 16-bit PCM. A clipped sample is exactly -32,768 or 32,767; near clipping means an absolute amplitude of at least 32,760. Edge silence is the consecutive leading or trailing 20 ms RMS windows below -45 dBFS. These edge-silence figures measure padding; they do not claim to measure background noise or signal-to-noise ratio.

| Reference | Duration (s) | Peak (dBFS) | RMS (dBFS) | Clipped / near-clipped samples | Leading / trailing quiet padding (s) |
| --- | ---: | ---: | ---: | ---: | ---: |
| VCTK p225 | 7.588146 | -3.013 | -21.886 | 0 / 0 | 0.840000 / 0.008146 |
| VCTK p237 | 7.172646 | -8.717 | -28.119 | 0 / 0 | 0.880000 / 0.932646 |
| VCTK p294 | 7.476938 | -8.079 | -27.791 | 0 / 0 | 0.960000 / 0.616937 |
| VCTK p326 | 6.999979 | -11.691 | -30.752 | 0 / 0 | 0.780000 / 0.539979 |

All four durations are within the 3–10 second reference range checked by [GPT-SoVITS's official inference implementation](https://github.com/RVC-Boss/GPT-SoVITS/blob/main/GPT_SoVITS/TTS_infer_pack/TTS.py). This source screening does not establish how closely a particular engine, model version, or requested text will reproduce a speaker. Listen to a synthesis trial before choosing a voice for a finished production.

`catalog.json` records each source archive member's CRC32 and SHA256, the corresponding original transcript's CRC32 and SHA256, the bundled WAV's SHA256, and the decoded PCM's SHA256. Source members were extracted using bounded HTTP byte ranges from the official ZIP; each selected member's decompressed size and ZIP CRC32 were verified. The full 11,747,302,977-byte archive was not downloaded and its whole-file hash was not verified. The copies of the publisher's README, license, and speaker metadata also have SHA256 entries in the catalog.
