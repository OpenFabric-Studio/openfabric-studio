"""Reviewed upstream declarations, not a licence audit of a user's local files.

Code, model weights, dependencies and source recordings retain separate terms.
These cards make known declarations and remaining unknowns visible; they do not
certify an installation, clear source rights or grant rights in generated media.
"""
from __future__ import annotations

from .module_contracts import LicenseScope, ModuleId, ModuleLicenseDeclaration

REVIEWED_AT = '2026-10-06'


def _declared(identifier: str, name: str, scope: LicenseScope, license_name: str, url: str,
              notes: str = 'Upstream declaration for the linked component. Other dependencies and local versions can have different terms.') -> ModuleLicenseDeclaration:
    return ModuleLicenseDeclaration(component_id=identifier, name=name, scope=scope,
        status='declared', declared_license=license_name, source_url=url, notes=notes, reviewed_at=REVIEWED_AT)


def _unknown(identifier: str, name: str, scope: LicenseScope, notes: str, url: str | None = None) -> ModuleLicenseDeclaration:
    return ModuleLicenseDeclaration(component_id=identifier, name=name, scope=scope, status='unknown',
        source_url=url, notes=notes, reviewed_at=REVIEWED_AT)


_DECLARATIONS: dict[ModuleId, tuple[ModuleLicenseDeclaration, ...]] = {
    'speaker_review': (
        _declared('speechbrain-code', 'SpeechBrain 1.0.3 source', 'code', 'Apache-2.0', 'https://github.com/speechbrain/speechbrain/blob/v1.0.3/LICENSE'),
        _declared('ecapa-voxceleb', 'ECAPA VoxCeleb published checkpoint', 'model', 'Apache-2.0', 'https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb', 'Publisher model-card declaration for the reviewed checkpoint. VoxCeleb source data and third-party dependencies have separate terms.'),
        _unknown('speaker-reference-data', 'Selected reference recordings and source data', 'source_data', 'Human approval of a reference is separate from permission to process or reuse its recording. No identity or commercial clearance follows from a similarity score.'),
    ),
    'ace_step': (
        _declared('ace-step-code', 'ACE-Step 1.5 source', 'code', 'MIT', 'https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/LICENSE'),
        _declared('ace-step-1.5', 'ACE-Step 1.5 published weights', 'model', 'MIT', 'https://huggingface.co/ACE-Step/Ace-Step1.5'),
        _unknown('ace-local-checkpoints', 'Custom checkpoints and auxiliary models', 'auxiliary', 'The app does not certify the terms or origin of local replacement checkpoints, text encoders or third-party adapters.'),
    ),
    'yue2': (
        _declared('audio-cpp', 'audio.cpp native engine', 'code', 'Apache-2.0', 'https://github.com/0xShug0/audio.cpp/blob/v0.8.1/LICENSE'),
        _unknown('yue2_main_q8_0', 'YuE2 main Q8_0 conversion', 'model', 'Exact converted package terms and source-weight provenance are unverified. The engine source licence does not establish weight rights.', 'https://github.com/0xShug0/audio.cpp/tree/v0.8.1/model_specs'),
        _unknown('yue2_main_q4_0', 'YuE2 main Q4_0 conversion', 'model', 'Exact converted package terms and source-weight provenance are unverified. The engine source licence does not establish weight rights.', 'https://github.com/0xShug0/audio.cpp/tree/v0.8.1/model_specs'),
        _unknown('yue2_vae_f16', 'YuE2 VAE F16 conversion', 'model', 'Review the exact VAE checkpoint and conversion terms separately; its package digest verifies bytes, not licensing.', 'https://github.com/0xShug0/audio.cpp/tree/v0.8.1/model_specs'),
        _unknown('sheetsage2_orig', 'SheetSage2 original package', 'model', 'The selected package model declaration and source-weight provenance are unverified.', 'https://github.com/0xShug0/audio.cpp/tree/v0.8.1/model_specs'),
        _unknown('muscriptor_small_f32', 'Muscriptor small F32 package', 'model', 'The selected package model declaration and source-weight provenance are unverified.', 'https://github.com/0xShug0/audio.cpp/tree/v0.8.1/model_specs'),
    ),
    'speech': (
        _declared('gpt-sovits-code', 'GPT-SoVITS source', 'code', 'MIT', 'https://github.com/RVC-Boss/GPT-SoVITS/blob/48b1a0169a28582a8984402f82cf438d3bfa6aca/LICENSE'),
        _declared('gpt-sovits-published', 'GPT-SoVITS published pretrained weights', 'model', 'MIT', 'https://huggingface.co/lj1995/GPT-SoVITS'),
        _unknown('speech-local-weights', 'Local fine-tunes and auxiliary checkpoints', 'auxiliary', 'A project code licence does not identify a local fine-tune, tokenizer, encoder, vocoder or its training recordings.'),
    ),
    'singing': (
        _declared('seed-vc-code', 'Seed-VC source', 'code', 'GPL-3.0', 'https://github.com/Plachtaa/seed-vc/blob/51383efd921027683c89e5348211d93ff12ac2a8/LICENSE'),
        _declared('seed-vc-published', 'Seed-VC published checkpoints', 'model', 'GPL-3.0', 'https://huggingface.co/Plachta/Seed-VC'),
        _unknown('singing-local-weights', 'Local training and auxiliary models', 'auxiliary', 'Review terms for your selected source recordings, fine-tunes, vocoder and speaker encoder separately.'),
    ),
    'separation': (
        _declared('demucs-code', 'Demucs source', 'code', 'MIT', 'https://github.com/facebookresearch/demucs/blob/main/LICENSE'),
        _unknown('separation-weights', 'Selected Demucs or RoFormer weights', 'model', 'The installed weight variant and training-data rights are not established by the code licence. RoFormer variants may have different terms.', 'https://github.com/facebookresearch/demucs'),
    ),
    'video': (
        _declared('ltx-mlx-code', 'Pinned LTX MLX integration', 'code', 'MIT', 'https://github.com/dgrauet/ltx-2-mlx/blob/1724ca673d59f023a8a95efee06e5d36d61c2765/LICENSE'),
        _declared('ltx-2.3', 'LTX 2.3 published weights', 'model', 'LTX-2 Community License', 'https://huggingface.co/Lightricks/LTX-2.3/blob/main/LICENSE', 'Custom model agreement with use and commercial restrictions. Read the full agreement; the integration code MIT licence does not replace these terms.'),
        _unknown('ltx-experimental-local', 'LTX 2.5, converted weights and auxiliary encoders', 'auxiliary', 'Opt-in experiments, converters, text encoders and local adapters need their own model declarations. No permission is inferred from a filename or successful inference.'),
    ),
    'media': (
        _declared('ffmpeg-code', 'FFmpeg / FFprobe upstream licensing', 'tool', 'LGPL-2.1-or-later / GPL (build dependent)', 'https://ffmpeg.org/legal.html', 'Enabled components and configure options determine the licence of a particular binary.'),
        _unknown('ffmpeg-installed-build', 'Installed FFmpeg binary and linked components', 'tool', 'The app has not audited this binary build, its configure options or every linked library. Inspect the distributor notices and build configuration.'),
    ),
    'transcription': (
        _declared('whisper-cpp', 'whisper.cpp source', 'code', 'MIT', 'https://github.com/ggml-org/whisper.cpp/blob/master/LICENSE'),
        _unknown('transcription-checkpoint', 'Configured transcript model', 'model', 'Review the exact checkpoint source and any conversion licence. A configured local path alone does not establish its terms.'),
    ),
    'source_import': (
        _declared('yt-dlp', 'yt-dlp source', 'tool', 'Unlicense', 'https://github.com/yt-dlp/yt-dlp/blob/master/LICENSE'),
        _declared('deno', 'Deno runtime source', 'tool', 'MIT', 'https://github.com/denoland/deno/blob/main/LICENSE.md'),
        _unknown('imported-media', 'Imported recordings and transcripts', 'source_data', 'Tool licensing and download access do not establish rights to reuse, train on or redistribute a recording or transcript. Review its source terms and permissions.'),
    ),
    'ebooks': (
        _declared('calibre', 'Calibre source', 'tool', 'GPL-3.0', 'https://github.com/kovidgoyal/calibre/blob/master/LICENSE'),
        _unknown('ebook-text', 'Imported book text and covers', 'source_data', 'Conversion support does not grant rights to narrate, reproduce or distribute the book or its cover.'),
    ),
    'kokoro': (
        _declared('kokoro-code', 'Kokoro source', 'code', 'Apache-2.0', 'https://github.com/hexgrad/kokoro/blob/dfb907a02bba8152ca444717ca5d78747ccb4bec/LICENSE'),
        _declared('kokoro-82m', 'Kokoro-82M published weights', 'model', 'Apache-2.0', 'https://huggingface.co/hexgrad/Kokoro-82M'),
        _unknown('kokoro-local-presets', 'Local preset files and espeak-ng', 'auxiliary', 'Verify local preset provenance and the separately installed speech tool terms.'),
    ),
    'chatterbox': (
        _declared('chatterbox-code', 'Chatterbox source', 'code', 'MIT', 'https://github.com/resemble-ai/chatterbox/blob/5de7a54aa4e5e2baadb0182dde554908b48b85c2/LICENSE'),
        _unknown('chatterbox-local-weights', 'Original / multilingual local checkpoints', 'model', 'Review the exact selected model card and checkpoint terms; the linked source licence alone does not establish local weight provenance.', 'https://huggingface.co/ResembleAI'),
    ),
    'wan22': (
        _declared('mlx-video', 'mlx-video source', 'code', 'MIT', 'https://github.com/Blaizzy/mlx-video/blob/87db56a51758fefb748a359b90a5283bb8ba4837/LICENSE'),
        _declared('wan22-ti2v-5b', 'Wan 2.2 TI2V-5B upstream weights', 'model', 'Apache-2.0', 'https://huggingface.co/Wan-AI/Wan2.2-TI2V-5B'),
        _unknown('wan22-conversion', 'Local MLX conversion and auxiliary models', 'auxiliary', 'Review conversion provenance and auxiliary model terms. This card does not certify a local converted folder.'),
    ),
    'rvc': (
        _declared('rvc-code', 'RVC source', 'code', 'MIT', 'https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI/blob/81eed5e8f68b6bed1789f682fe78cdd324495afc/LICENSE'),
        _unknown('rvc-voice-model', 'Local trained RVC voice', 'model', 'Each voice model and its source recordings need their own provenance and permission. RVC code MIT does not certify those rights.'),
        _unknown('rvc-auxiliary', 'HuBERT and RMVPE checkpoints', 'auxiliary', 'Review the exact downloaded checkpoint model cards separately from the RVC source licence.'),
    ),
}


def declarations(identifier: ModuleId) -> list[ModuleLicenseDeclaration]:
    return [record.model_copy(deep=True) for record in _DECLARATIONS[identifier]]
