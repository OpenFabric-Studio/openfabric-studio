# Native ACE training audio regression sources

These are the unchanged preprocessing helper and training data module from
[ACE-Step-1.5](https://github.com/ace-step/ACE-Step-1.5), commit
`ca1e85fe9430179831e6bc6be790c332190a3866`. Their upstream MIT terms and
attribution are preserved in `LICENSE-MIT`.

Tests apply the maintained patch in a temporary source tree and exercise only
audio preprocessing and the raw dataset item function, with tensor operations
mocked. They never import the engine, load models, or access user libraries.
