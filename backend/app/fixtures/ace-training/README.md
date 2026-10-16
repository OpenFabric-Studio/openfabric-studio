# Native ACE numerical training regression sources

`trainer.py.txt` retains exact numerical helpers, autocast selection branches,
and LoRA/LoKr training loops from [ACE-Step-1.5](https://github.com/ace-step/ACE-Step-1.5),
commit `ca1e85fe9430179831e6bc6be790c332190a3866`. Setup before each epoch loop is
replaced with local scalar initializers; model/optimizer/scheduler construction
is omitted. The public training wrappers retain their upstream exception and
cleanup branches around an injected loop. Upstream MIT terms and attribution
are preserved in `LICENSE-MIT`.

Tests apply the maintained trainer patch and exercise the actual accumulation,
numerical failure, checkpoint and completion branches with tensor/optimizer
boundaries mocked. No engine, models, GPU, server or user library is accessed.
