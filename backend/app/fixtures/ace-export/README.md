# Native ACE export regression sources

These fixtures retain the training export route, Gradio export functions and path sanitizer from [ACE-Step-1.5](https://github.com/ace-step/ACE-Step-1.5), commit `ca1e85fe9430179831e6bc6be790c332190a3866`. The Gradio fixtures contain the original import region and export function only. Their upstream MIT terms and attribution are preserved in `LICENSE-MIT`.

Tests apply the maintained patch in a temporary source tree and execute only the export functions and standard-library safety helper. They never import the engine, load models, call a running server, or access user libraries.
