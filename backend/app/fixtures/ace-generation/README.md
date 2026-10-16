# Native ACE diffusion regression source

This unchanged execution mixin comes from [ACE-Step-1.5](https://github.com/ace-step/ACE-Step-1.5),
commit `ca1e85fe9430179831e6bc6be790c332190a3866`. Its upstream MIT terms and
attribution are preserved in `LICENSE-MIT`.

Tests apply the maintained patch in a temporary tree, then exercise backend
selection with mocked model/tensor boundaries. They never import the engine,
load models, or access the user's library.
