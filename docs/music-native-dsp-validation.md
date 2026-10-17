# Native stretch validation

Observed on 2026-10-17 on this Apple Silicon Mac. These synthetic checks establish the stated timing, pitch, headroom and stereo behavior only. They do not establish listening quality, formant preservation or Linux/Windows runtime support.

The [Rubber Band publisher](https://breakfastquay.com/rubberband/) provides a [4.0.0 macOS executable archive](https://breakfastquay.com/files/releases/rubberband-4.0.0-gpl-executable-macos.tar.bz2). The archive was 1,500,014 bytes with observed SHA-256 `0dc91509a31a94c1144436cc20e6f1d165bdab39fe125f9be7d46139921b733f`; its universal primary executable had observed SHA-256 `bffe8a37f204958352e768fc4a751d3dbce18e3b42c2a3427a80498e7d8cee79` and returned `4.0.0`. These are observed pins, not publisher-signed checksum claims. The inspected source is [v4.0.0 main.cpp](https://github.com/breakfastquay/rubberband/blob/v4.0.0/main/main.cpp), with [GPL-2.0-or-later terms](https://github.com/breakfastquay/rubberband/blob/v4.0.0/COPYING). The source archive's observed SHA-256 was `af050313ee63bc18b35b2e064e5dce05b276aaf6d1aa2b8a82ced1fe2f8028e9`.

No Homebrew package, global executable, live module configuration or music model was installed. The proof used a separate temporary directory containing only synthetic audio and these publisher artifacts.

| Actual native check | Observed result |
| --- | --- |
| R3 three-second identity, 48 kHz stereo antiphase tones | 144,000 exact frames; opposite-channel cancellation error zero |
| R3 uniform 1.25 duration ratio | 180,000 exact frames; dominant 440 Hz retained; cancellation error zero |
| R3 non-uniform map, expected 162,000 frames | **Failed:** successful exit but 154,492 frames, short by 7,508; rejected rather than padded |
| R2 same non-uniform map, pitch 0 and +12 semitones | 162,000 exact frames; +12 output peaks near 880.59/1320.89 Hz from 440/660 Hz inputs |
| R2 non-uniform stereo transient map through the guarded adapter | 162,000 exact frames; transient peak offsets 1.31, 2.35 and 3.35 ms; cancellation error zero |
| R3 ten-minute input → 12½-minute output | 36,000,000 exact frames; cancellation error zero; 21.216 s elapsed; native child maximum RSS 10,862,592 bytes |
| Same ten-minute transform through guarded app adapter | 36,000,000 exact frames; 21.957 s elapsed including streamed validation and scaling |
| Ten-minute R2 non-uniform transient map through the guarded adapter | 32,400,000 exact frames; stereo cancellation error zero; 1.682 s elapsed. **The ≤5 ms transient check failed:** maximum observed offset 5.458 ms across twenty transients. The non-uniform core limit remains 30 seconds. |
| Over-full-scale float input with explicit power-of-two attenuation and inverse compensation | Output float peak about 5.038 retained rather than clipped to one |

The recipe selects R3 for a uniform frame map and R2 for a non-uniform map. It supplies `--centre-focus` for linked stereo and uses `--ignore-clipping` to prevent hidden gain restarts. That flag itself clamps full-scale output, so the adapter first reserves explicit headroom and rejects any native sample at or above full scale. Inverse scaling restores the recorded gain into float PCM. The exact recipe and gain powers are retained in engine evidence.

Headroom compensation is repeatable but **not bit-equivalent** to unattenuated native execution. In a tone probe, power −4 plus inverse compensation differed by about 0.15% RMS; repeated runs of the same recipe were bit-identical. Native algorithm thresholds may respond to gain. This is a recorded processing choice, not a claim of transparent mathematical equivalence.

Unit tests also mock wrong-version/hash changes, pre-existing destinations, collapsed/out-of-range frame maps, nonfinite/clamped results and short native output. The native binary is not downloaded by tests. Actual probes are intentionally separate from CI.

An actual managed-install probe used the existing setup job service and supervisor in a separate temporary runtime root, reusing previously downloaded publisher archives. Both Media tools and Music processing verified; all child processes drained; the next plan selected reuse. Its installed files included the pinned executable, notices and source archive. No live runtime was modified. Runtime unit tests cover modified notices/binary, existing destinations, unsafe archive links, changed source archives, cancellation before promotion, unsupported platforms and symlinked runtime roots.
