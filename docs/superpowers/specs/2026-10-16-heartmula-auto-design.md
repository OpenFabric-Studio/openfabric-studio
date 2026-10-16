# HeartMuLa automatic generation settings

> Historical record — HeartMuLa retired on 16 October 2026. Its generation, training and installation instructions below no longer describe supported functionality. See [current music documentation](../../local-music.md).

Approved on 16 October 2026: add Auto settings and test longer generation on this Mac.

Auto is the default on the HeartMuLa generator. It uses the existing runtime sampling defaults, a backend-generated random seed and natural audio EOS. It selects the tested duration ceiling, displayed as a maximum rather than a promised song length. Manual duration and seed remain available. Adapter choice and strength stay explicit.

The supported longer ceiling is 480 seconds, gated on actual local generation and decoding evidence for base and adapter runtimes. A base test requested 600 seconds and ended naturally at 483.92 seconds; that supports an eight-minute ceiling, not a ten-minute claim. Preserve the 600-second technical parsing/worker bound so historical test jobs remain readable. Both workers validate the actual tokenized prompt against an 8,192-position context at 12.5 audio frames per second. Auto reduces its ceiling to fit the context; manual requests that exceed the context fail before loading the model. Never ignore EOS in the product or infer audio quality from successful decoding.

Persist the chosen mode, seed and maximum with each job so reconnect/recovery does not change them. Existing requests/history default to manual. Stable Audio limits remain model-specific. The music queue uses the same tested HeartMuLa ceiling for its existing manual-duration normalization. No installs, new model downloads, migrations or cleanup are part of this change.

Verification uses isolated unit tests and real, bounded Mac generation under the app's resource ownership. Preserve existing user work and outputs. Record duration, wall time, memory evidence and EOS behavior honestly; early EOS alone does not prove the requested maximum. No commit or push is authorized.
