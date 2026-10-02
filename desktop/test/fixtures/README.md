# Pinned audio.cpp downloader fixture

`model_manager_v2.py` is an unchanged test fixture from [audio.cpp at 39f9013463053e206aa160f8453d734f78999b9d](https://github.com/0xShug0/audio.cpp/blob/39f9013463053e206aa160f8453d734f78999b9d/tools/model_manager_v2.py). It is byte-identical to the desktop `v0.8.1` downloader. SHA256: `da8137c59161a28b2a5f99fd691f01dd1c3b6bb4a96bed9f2f86378d07737d88`.

Copyright 2026 ShugoAI LLC. The included [audio.cpp-LICENSE](audio.cpp-LICENSE) applies to that third-party source. Existing legacy typing is preserved for patch applicability; this fixture is not an app-owned schema or production module.

The Node harness copies the fixture to a temporary engine, applies `yue-model-resume.patch`, and runs `test_model_resume.py` against fake responses and temporary model paths. Model providers, GPU engines, and private configuration are not required.
