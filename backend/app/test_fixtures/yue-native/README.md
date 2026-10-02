# Pinned YuE native sources

`sources.json.gz` contains the original four patched YuE C++ source files
(`ar_runtime.cpp`, `nar_runtime.cpp`, `pipeline.cpp`, `session.cpp`) from official
[`0xShug0/audio.cpp`](https://github.com/0xShug0/audio.cpp), at source commit
`39f9013463053e206aa160f8453d734f78999b9d` and desktop `v0.8.1` commit
`f2b4937306daa25f5c78520f3c626ed31495a37a`. The JSON maps exact commit IDs to
file paths and unmodified file contents. The gzip timestamp is zero so the
fixture can be regenerated reproducibly.

The fixture permits offline application of the complete patches, followed by
compiling the optional telemetry header in a model-free C++17 probe. These are
source/ownership checks; they do not establish generated audio quality.

The upstream Apache-2.0 license is preserved as `UPSTREAM_LICENSE`.
