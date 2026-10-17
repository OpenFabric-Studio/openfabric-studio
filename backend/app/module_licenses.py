"""Reviewed upstream declarations, not a licence audit of a user's local files.

Code, model weights, dependencies and source recordings retain separate terms.
These cards make known declarations and remaining unknowns visible; they do not
certify an installation, clear source rights or grant rights in generated media.
"""
from __future__ import annotations

from typing import Literal

from .module_contracts import (
    LicenseCopyleft, LicenseRedistribution, LicenseRestriction, LicenseScope,
    ModuleComponentIdentity, ModuleId, ModuleLicenseDeclaration, ModuleLicenseEvidence,
)

REVIEWED_AT = '2026-10-06'
MUSIC_REVIEWED_AT = '2026-10-17'


def _identity(repository: str, revision: str, filename: str, sha256: str) -> ModuleComponentIdentity:
    return ModuleComponentIdentity(repository_url='https://huggingface.co/' + repository,
        revision=revision, filename=filename, sha256=sha256)


def _observed(url: str, revision: str, sha256: str, *,
              kind: Literal['license_file', 'publisher_model_card', 'publisher_terms'] = 'publisher_model_card') -> ModuleLicenseEvidence:
    return ModuleLicenseEvidence(kind=kind, source_url=url, revision=revision, sha256=sha256,
        observed_at=MUSIC_REVIEWED_AT, reviewer='OpenFabric Studio primary-source review')


# Public publisher metadata checked at these revisions; no installed-byte claim.
_ACE_REPOSITORY = 'ACE-Step/Ace-Step1.5'
_ACE_REVISION = '19671f406d603126926c1b7e2adc169acbcade22'
_STABLE_REPOSITORY = 'stabilityai/stable-audio-3-optimized'
_STABLE_GENERATION = '08b17423649b5714b22eab20f96bf00751f2358a'
_STABLE_TRAINING = '08c64b96b1e59942aade69759f60fb88c58c90c4'
_YUE_REPOSITORY = 'audio-cpp/Yue2-3B-GGUF'
_YUE_REVISION = 'eb14a51700bf8baac825a690b2f3b24a01239f85'
_SHEET_REPOSITORY = 'audio-cpp/SheetSage2-GGUF'
_SHEET_REVISION = '4e88814450385334524e50df43724af583f74206'
_MUSCRIPTOR_REPOSITORY = 'audio-cpp/audio.cpp-gguf'
_MUSCRIPTOR_REVISION = 'f861648c0c1f4da9f18e9a0ce5e217b0679bb2f5'

_MUSIC_IDENTITIES: dict[str, tuple[ModuleComponentIdentity, ...]] = {
    'ace-step-1.5': (
        _identity('ACE-Step/acestep-v15-base', 'e432212fec32b8965a14ffa57ae653438d6abd14', 'model.safetensors', '4177f600501a6d4bd81cadaa0abac557ffd15c54e5c8cb52053cdb24a0844d6b'),
        _identity(_ACE_REPOSITORY, _ACE_REVISION, 'acestep-5Hz-lm-1.7B/model.safetensors', 'f161689da73e5ecefa28ff780d51c2d92a00f056d021d7933c779ed5c6cd7db8'),
        _identity(_ACE_REPOSITORY, _ACE_REVISION, 'acestep-v15-turbo/model.safetensors', '3f6e0797fad420a39bd33979eb6e840e30989e34a3794e843d23b60ec6e422d7'),
        _identity(_ACE_REPOSITORY, _ACE_REVISION, 'vae/diffusion_pytorch_model.safetensors', 'da17edb604c40deaf09e9b24974e590d1ca83a374070e5d0884cfa4bed9a99b0'),
        _identity('ACE-Step/acestep-v15-xl-sft', 'd06de46b4622f781cf07f4a013a67d591ca52819', 'model-00001-of-00004.safetensors', '1d304b8a34859cd92c349c71ae6c27d50564844c7f7611d2d32913f67508e5a4'),
        _identity('ACE-Step/acestep-v15-xl-sft', 'd06de46b4622f781cf07f4a013a67d591ca52819', 'model-00002-of-00004.safetensors', '94e33648f514fdd00f473f2d2680af959204b58b15f7ab37246ac16d066aac87'),
        _identity('ACE-Step/acestep-v15-xl-sft', 'd06de46b4622f781cf07f4a013a67d591ca52819', 'model-00003-of-00004.safetensors', 'befe558365f53bc6c44f3f7e521cb2dd026927a817c36aa05ad94fcd80a533a9'),
        _identity('ACE-Step/acestep-v15-xl-sft', 'd06de46b4622f781cf07f4a013a67d591ca52819', 'model-00004-of-00004.safetensors', 'dfbc77aa26e54d0127892d6e7f3c0868bfc4623aa067867f6595c3b2fb7d31a4'),
        _identity('ACE-Step/acestep-5Hz-lm-4B', '0a3ec94b557aea7d508da38b31cfe7341f6ff737', 'model-00001-of-00002.safetensors', 'ada9d0d4ff48f112de3f7b82cd4e7d57b4245932657e8b8edc9a5ded6a23b77f'),
        _identity('ACE-Step/acestep-5Hz-lm-4B', '0a3ec94b557aea7d508da38b31cfe7341f6ff737', 'model-00002-of-00002.safetensors', '6302100c3577e2f1dbf32573e9b5e6e6b1bea7af101b433c2d3d6280faa8ab68'),
    ),
    'ace-step-qwen3-embedding': (
        _identity(_ACE_REPOSITORY, _ACE_REVISION, 'Qwen3-Embedding-0.6B/model.safetensors', '0437e45c94563b09e13cb7a64478fc406947a93cb34a7e05870fc8dcd48e23fd'),
    ),
    'stable-audio3-optimized': (
        _identity(_STABLE_REPOSITORY, _STABLE_GENERATION, 'MLX/dit_sm-music_f16.npz', '8ed3f38e2597f361ee675051f1265d9aa2ae2fffce1c61acd2e9fe31e1db1cbc'),
        _identity(_STABLE_REPOSITORY, _STABLE_GENERATION, 'MLX/same_s_decoder_f32.npz', '909928a8e6937c1ebe6ac4b729f0462bd3773704a11ea18278e42671dc69bfe4'),
        _identity(_STABLE_REPOSITORY, _STABLE_GENERATION, 'MLX/dit_medium_f16.npz', 'f9e5647ea3225818657d47d47ae4b34afa29c0568206ca89566c1a758944a38e'),
        _identity(_STABLE_REPOSITORY, _STABLE_GENERATION, 'MLX/same_l_decoder_f32.npz', '84924be2122d3a20fce443f40b782d9cd88e8e73707476326003ac47659a2287'),
        _identity(_STABLE_REPOSITORY, _STABLE_TRAINING, 'MLX/dit_sm-music-base_f16.npz', '4a8ee6dc2f5ef3fbaa595cb337f1c0718c0fbc1916071f929f78beda4335e231'),
        _identity(_STABLE_REPOSITORY, _STABLE_TRAINING, 'MLX/same_s_encoder_f32.npz', 'a48f80d81c30d74c45e2a3047082c4891f715e24c44645adb9c1f4f07afdaf0c'),
        _identity(_STABLE_REPOSITORY, _STABLE_TRAINING, 'MLX/dit_medium-base_f16.npz', 'cb829e33d7e64fe07cd190acb1231a9716be11e58eacc04e835e0652dd888931'),
        _identity(_STABLE_REPOSITORY, _STABLE_TRAINING, 'MLX/same_l_encoder_f32.npz', 'c5caafc6bd29fc3d4cb7a08b0d3725041fa2100711f86a4f05d2638c532871d2'),
    ),
    'stable-audio3-t5gemma': (
        _identity(_STABLE_REPOSITORY, _STABLE_GENERATION, 'MLX/t5gemma_f16.npz', '8deb20489f36d9aec539f26c9c67321f99bc5fe300d470435ed6e76be4f16bbd'),
        _identity(_STABLE_REPOSITORY, _STABLE_TRAINING, 'MLX/t5gemma_f16.npz', '8deb20489f36d9aec539f26c9c67321f99bc5fe300d470435ed6e76be4f16bbd'),
    ),
    'yue2_main_q8_0': (_identity(_YUE_REPOSITORY, _YUE_REVISION, 'yue2-3b-q8_0.gguf', '1bbbfeea90c6219afb0c73b6a212ce7847ca6a982534388064f8e61e031b6a3f'),),
    'yue2_main_q4_0': (_identity(_YUE_REPOSITORY, _YUE_REVISION, 'yue2-3b-q4_0.gguf', 'cb1f76c8c90755a4c5c9cc0220b26b914570d74f4dc3ec6599469a46d7990f4b'),),
    'yue2_vae_f16': (_identity(_YUE_REPOSITORY, _YUE_REVISION, 'yue2-vae-f16.gguf', 'd4f4a05d8f291ae820cd1e43609da3fa91b56465810091a2b08c3350b751719d'),),
    'sheetsage2_orig': (_identity(_SHEET_REPOSITORY, _SHEET_REVISION, 'sheetsage2-orig.gguf', 'd858bfdf5ac7be16261ac6ee38754d6fca502149131b44a5262b4039fb72876e'),),
    'muscriptor_small_f32': (_identity(_MUSCRIPTOR_REPOSITORY, _MUSCRIPTOR_REVISION, 'MuScriptor-Small-GGUF/muscriptor-small-f32.gguf', '4bff6f1c45b445e144c176434fbe27614ad6c4686e03f99ee11b17e15163fba2'),),
}

_YUE_EVIDENCE = (_observed(
    'https://huggingface.co/audio-cpp/Yue2-3B-GGUF/raw/' + _YUE_REVISION + '/README.md',
    _YUE_REVISION, '49b1045caa920190b768b5b9c16af6095bae5083b9c76768d31f69221e2043eb'),)
_MUSIC_EVIDENCE: dict[str, tuple[ModuleLicenseEvidence, ...]] = {
    'beat-this-code-mit': (_observed('https://raw.githubusercontent.com/CPJKU/beat_this/ad7974846029835307ba19a3d5cefbf40b243041/LICENSE',
        'ad7974846029835307ba19a3d5cefbf40b243041', '909ab6549794a18e9bb243aacfadda4a5f308436fc2846f350755c53c4f06ae1', kind='license_file'),),
    'beat-this-small0-weights-mit': (_observed('https://raw.githubusercontent.com/CPJKU/beat_this/ad7974846029835307ba19a3d5cefbf40b243041/README.md',
        'ad7974846029835307ba19a3d5cefbf40b243041', 'c9a2f5d91fe561be815122d77496d6e774cb98744d8128693d25b09f5202959d'),),
    'rubberband-4.0.0-gpl2-or-later': (_observed('https://raw.githubusercontent.com/breakfastquay/rubberband/v4.0.0/COPYING', 'v4.0.0', 'cedd4c1885d75991bf1077cc6e33359103cf04fbc996f20d30ab043d5fc5fcc5', kind='license_file'),),
    'stable-audio3-code': (_observed(
        'https://raw.githubusercontent.com/Stability-AI/stable-audio-3/77e0398c26f7d22d801ec1bb036307e8b231f741/LICENSE',
        '77e0398c26f7d22d801ec1bb036307e8b231f741', '16bd922f0deee6f11a76f5582258fdc3abdf67c6b8719dbcafbc34dee31979a6', kind='license_file'),),
    'stable-audio3-optimized': (
        _observed('https://huggingface.co/stabilityai/stable-audio-3-optimized/raw/' + _STABLE_GENERATION + '/README.md',
            _STABLE_GENERATION, '2289933688057cd1c51f11f1a0443fc7fbb341fd45276255fc85e7603bbe1a36'),
        _observed('https://huggingface.co/stabilityai/stable-audio-3-optimized/raw/' + _STABLE_TRAINING + '/README.md',
            _STABLE_TRAINING, '775ba6a36bbe7b7180d8c038c4549205ca54b586f0f1e073b6c643820f76f086'),
        _observed('https://huggingface.co/stabilityai/stable-audio-3-optimized/raw/' + _STABLE_GENERATION + '/LICENSE.md',
            _STABLE_GENERATION, 'd6f6b1a4dce5c852bd6d7d9482d002baf0ccdb71e662250b73be9eec8764ee8d', kind='publisher_terms'),
        _observed('https://huggingface.co/stabilityai/stable-audio-3-optimized/raw/' + _STABLE_TRAINING + '/LICENSE.md',
            _STABLE_TRAINING, 'd6f6b1a4dce5c852bd6d7d9482d002baf0ccdb71e662250b73be9eec8764ee8d', kind='publisher_terms'),
    ),
    'stable-audio3-t5gemma': (
        _observed('https://huggingface.co/stabilityai/stable-audio-3-optimized/raw/' + _STABLE_GENERATION + '/LICENSE_GEMMA.md',
            _STABLE_GENERATION, 'e77acc0d3163bb7534675045c584b4d04b387b529239fc4b3647da0a01ba4745', kind='publisher_terms'),
        _observed('https://huggingface.co/stabilityai/stable-audio-3-optimized/raw/' + _STABLE_TRAINING + '/LICENSE_GEMMA.md',
            _STABLE_TRAINING, 'e77acc0d3163bb7534675045c584b4d04b387b529239fc4b3647da0a01ba4745', kind='publisher_terms'),
    ),
    'ace-step-code': (_observed(
        'https://raw.githubusercontent.com/ace-step/ACE-Step-1.5/ca1e85fe9430179831e6bc6be790c332190a3866/LICENSE',
        'ca1e85fe9430179831e6bc6be790c332190a3866', '05a6bce42a62636d2cfb24139cc008b6b899754e244175814bb5dd2f4a485357', kind='license_file'),),
    'ace-step-1.5': (
        _observed('https://huggingface.co/ACE-Step/acestep-v15-base/raw/e432212fec32b8965a14ffa57ae653438d6abd14/README.md',
            'e432212fec32b8965a14ffa57ae653438d6abd14', 'ed42093e8f7903f23bd45ea56bea8f484c8aee47820ae8880d6da752681a1da7'),
        _observed('https://huggingface.co/ACE-Step/Ace-Step1.5/raw/' + _ACE_REVISION + '/README.md',
            _ACE_REVISION, '69d69b89e313706b93b420b13aa6232259b207a1cd19b0b70358d166e8e451a0'),
        _observed('https://huggingface.co/ACE-Step/acestep-v15-xl-sft/raw/d06de46b4622f781cf07f4a013a67d591ca52819/README.md',
            'd06de46b4622f781cf07f4a013a67d591ca52819', '6cfd536f953f5171283d84baf223c68d88979d8215cac9209324d04c331988e2'),
        _observed('https://huggingface.co/ACE-Step/acestep-5Hz-lm-4B/raw/0a3ec94b557aea7d508da38b31cfe7341f6ff737/README.md',
            '0a3ec94b557aea7d508da38b31cfe7341f6ff737', 'ed42093e8f7903f23bd45ea56bea8f484c8aee47820ae8880d6da752681a1da7'),
    ),
    'ace-step-qwen3-embedding': (_observed(
        'https://huggingface.co/Qwen/Qwen3-Embedding-0.6B/raw/97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3/README.md',
        '97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3', 'c34d9b7e5a267ad3fdd13227a253686bc90844ff4744a2a6a86c7c905e3d06f3'),),
    'audio-cpp': (_observed('https://raw.githubusercontent.com/0xShug0/audio.cpp/v0.8.1/LICENSE',
        'v0.8.1', '8f0b762d062e1ec79a3e73189b29474051c0e957973f43146aefeb03f2e11581', kind='license_file'),),
    'yue2_main_q8_0': _YUE_EVIDENCE,
    'yue2_main_q4_0': _YUE_EVIDENCE,
    'yue2_vae_f16': _YUE_EVIDENCE,
    'sheetsage2_orig': (_observed(
        'https://huggingface.co/audio-cpp/SheetSage2-GGUF/raw/' + _SHEET_REVISION + '/README.md',
        _SHEET_REVISION, '8bac420025454f9a699d31cca33a8022078810540c653cb84569a2e49dcabb3e'),),
    'muscriptor_small_f32': (_observed(
        'https://huggingface.co/audio-cpp/audio.cpp-gguf/raw/' + _MUSCRIPTOR_REVISION + '/README.md',
        _MUSCRIPTOR_REVISION, 'c8fe64b5fb80d143caf54e1638a94b017feadb390d8cba49d99af8e08d86b9a2'),),
}


def _declared(identifier: str, name: str, scope: LicenseScope, license_name: str, url: str,
              notes: str = 'Upstream declaration for the linked component. Other dependencies and local versions can have different terms.',
              reviewed_at: str = REVIEWED_AT, *, restriction: LicenseRestriction = 'unknown',
              redistribution: LicenseRedistribution = 'unknown', copyleft: LicenseCopyleft = 'unknown') -> ModuleLicenseDeclaration:
    return ModuleLicenseDeclaration(component_id=identifier, name=name, scope=scope,
        status='declared', declared_license=license_name, source_url=url, notes=notes, reviewed_at=reviewed_at,
        restriction=restriction, redistribution=redistribution, copyleft=copyleft, evidence=list(_MUSIC_EVIDENCE.get(identifier, ())),
        identities=list(_MUSIC_IDENTITIES.get(identifier, ())))


def _unknown(identifier: str, name: str, scope: LicenseScope, notes: str, url: str | None = None,
             reviewed_at: str = REVIEWED_AT) -> ModuleLicenseDeclaration:
    return ModuleLicenseDeclaration(component_id=identifier, name=name, scope=scope, status='unknown',
        source_url=url, notes=notes, reviewed_at=reviewed_at)


_DECLARATIONS: dict[ModuleId, tuple[ModuleLicenseDeclaration, ...]] = {
    'music_beats': (
        _declared('beat-this-code-mit', 'Beat This! 1.1.0 inference code', 'code', 'MIT',
            'https://github.com/CPJKU/beat_this/blob/ad7974846029835307ba19a3d5cefbf40b243041/LICENSE',
            'Pinned publisher code license. Installed dependencies retain their own terms.', reviewed_at=MUSIC_REVIEWED_AT,
            restriction='unrestricted_declared', redistribution='declared_allowed', copyleft='none_declared'),
        _declared('beat-this-small0-weights-mit', 'Beat This! small0 published checkpoint', 'model', 'MIT',
            'https://github.com/CPJKU/beat_this/blob/ad7974846029835307ba19a3d5cefbf40b243041/README.md#license',
            'Publisher declares published checkpoints MIT. Training recordings have separate rights. The acquisition receipt records publisher SHA-1 and an observed local SHA-256 separately; the latter is not a publisher SHA-256 declaration. Timing accuracy is unverified.',
            reviewed_at=MUSIC_REVIEWED_AT, restriction='unrestricted_declared', redistribution='declared_allowed', copyleft='none_declared'),
    ),
    'music_dsp': (
        _declared('rubberband-4.0.0-gpl2-or-later', 'Rubber Band 4.0.0 native tool', 'tool', 'GPL-2.0-or-later', 'https://github.com/breakfastquay/rubberband/blob/v4.0.0/COPYING', 'Publisher executable and corresponding source archives are pinned by observed SHA-256. The managed installation retains COPYING, notices and the corresponding source archive. Tool licensing does not establish rights to input recordings or generated output.', reviewed_at=MUSIC_REVIEWED_AT, restriction='unrestricted_declared', redistribution='declared_allowed', copyleft='strong'),
    ),
    'stable_audio3': (
        _declared('stable-audio3-code', 'Official Stable Audio 3 source and MLX runtime', 'code', 'MIT', 'https://github.com/Stability-AI/stable-audio-3/blob/77e0398c26f7d22d801ec1bb036307e8b231f741/LICENSE', reviewed_at=MUSIC_REVIEWED_AT,
            restriction='unrestricted_declared', redistribution='declared_allowed', copyleft='none_declared'),
        _declared('stable-audio3-optimized', 'Stable Audio 3 optimized music and codec weights', 'model', 'Stability AI Community License',
            'https://huggingface.co/stabilityai/stable-audio-3-optimized/blob/' + _STABLE_GENERATION + '/LICENSE.md',
            'Generation and training pins retain publisher conditions, registration and redistribution obligations. Read the exact agreement; source MIT and publisher training-data claims do not clear source rights or certify local bytes.',
            reviewed_at=MUSIC_REVIEWED_AT, restriction='conditional'),
        _declared('stable-audio3-t5gemma', 'T5Gemma text encoder', 'auxiliary', 'Gemma Terms of Use',
            'https://huggingface.co/stabilityai/stable-audio-3-optimized/blob/' + _STABLE_GENERATION + '/LICENSE_GEMMA.md',
            'Publisher redistributes T5Gemma under the separate Gemma Terms of Use, including prohibited-use and redistribution obligations. Expected payload identities do not certify local replacements or training rights.',
            reviewed_at=MUSIC_REVIEWED_AT, restriction='conditional'),
        _unknown('stable-audio3-local-bundles', 'Local optimized bundles and conversions', 'model', 'The app checks required local artifacts without clearing their rights or certifying a replacement checkpoint.', reviewed_at='2026-10-14'),
    ),
    'speaker_review': (
        _declared('speechbrain-code', 'SpeechBrain 1.0.3 source', 'code', 'Apache-2.0', 'https://github.com/speechbrain/speechbrain/blob/v1.0.3/LICENSE'),
        _declared('ecapa-voxceleb', 'ECAPA VoxCeleb published checkpoint', 'model', 'Apache-2.0', 'https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb', 'Publisher model-card declaration for the reviewed checkpoint. VoxCeleb source data and third-party dependencies have separate terms.'),
        _unknown('speaker-reference-data', 'Selected reference recordings and source data', 'source_data', 'Human approval of a reference is separate from permission to process or reuse its recording. No identity or commercial clearance follows from a similarity score.'),
    ),
    'ace_step': (
        _declared('ace-step-code', 'ACE-Step 1.5 source', 'code', 'MIT', 'https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/LICENSE', reviewed_at=MUSIC_REVIEWED_AT,
            restriction='unrestricted_declared', redistribution='declared_allowed', copyleft='none_declared'),
        _declared('ace-step-1.5', 'ACE-Step 1.5 published weights', 'model', 'MIT',
            'https://huggingface.co/ACE-Step/Ace-Step1.5/blob/' + _ACE_REVISION + '/README.md',
            'Publisher model-card declarations for the pinned bundle, base, XL SFT and 4B LM. Expected hashes matched public metadata, without downloading weights. Encoder, source-data and local replacement rights remain separate.',
            reviewed_at=MUSIC_REVIEWED_AT, restriction='unrestricted_declared', redistribution='declared_allowed', copyleft='none_declared'),
        _declared('ace-step-qwen3-embedding', 'Qwen3 Embedding 0.6B text encoder', 'auxiliary', 'Apache-2.0',
            'https://huggingface.co/Qwen/Qwen3-Embedding-0.6B/blob/97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3/README.md',
            'Original encoder declaration is separate from the ACE bundle MIT label. Identity records the republished payload selected by the current installer; no source-recording permissions are inferred.',
            reviewed_at=MUSIC_REVIEWED_AT, restriction='unrestricted_declared', redistribution='declared_allowed', copyleft='none_declared'),
        _unknown('ace-local-checkpoints', 'Custom checkpoints and auxiliary models', 'auxiliary', 'The app does not certify the terms or origin of local replacement checkpoints, text encoders or third-party adapters.'),
        _unknown('ace-source-rights', 'ACE source recordings, compositions and references', 'source_data',
            'Permissive code and weight declarations and publisher data claims do not establish recording, composition, performer consent or output release rights.', reviewed_at=MUSIC_REVIEWED_AT),
    ),
    'yue2': (
        _declared('audio-cpp', 'audio.cpp native engine', 'code', 'Apache-2.0', 'https://github.com/0xShug0/audio.cpp/blob/v0.8.1/LICENSE', reviewed_at=MUSIC_REVIEWED_AT,
            restriction='unrestricted_declared', redistribution='declared_allowed', copyleft='none_declared'),
        _declared('yue2_main_q8_0', 'YuE2 main Q8_0 conversion', 'model', 'CC-BY-NC-4.0', 'https://huggingface.co/audio-cpp/Yue2-3B-GGUF/blob/' + _YUE_REVISION + '/README.md',
            'Restricted for commercial production absent separate permission. Public pinned metadata identifies expected bytes; the 2026-10-14 installed-hash audit remains separate.', reviewed_at=MUSIC_REVIEWED_AT, restriction='noncommercial'),
        _declared('yue2_main_q4_0', 'YuE2 main Q4_0 conversion', 'model', 'CC-BY-NC-4.0', 'https://huggingface.co/audio-cpp/Yue2-3B-GGUF/blob/' + _YUE_REVISION + '/README.md',
            'Restricted for commercial production absent separate permission; code Apache-2.0 does not clear these weights.', reviewed_at=MUSIC_REVIEWED_AT, restriction='noncommercial'),
        _declared('yue2_vae_f16', 'YuE2 VAE F16 conversion', 'model', 'CC-BY-NC-4.0', 'https://huggingface.co/audio-cpp/Yue2-3B-GGUF/blob/' + _YUE_REVISION + '/README.md',
            'The converted package has noncommercial terms. Local replacements and source rights require independent review.', reviewed_at=MUSIC_REVIEWED_AT, restriction='noncommercial'),
        _declared('sheetsage2_orig', 'SheetSage2 original package with merged MERT2 backbone', 'model', 'CC-BY-NC-4.0',
            'https://huggingface.co/audio-cpp/SheetSage2-GGUF/blob/' + _SHEET_REVISION + '/README.md',
            'Restricted for commercial production absent separate permission, including the merged MERT2 backbone. Pinned API payload SHA-256 d858bfdf differs from the README historical 52bb5846 hash and size; expected API identity does not certify an older local file.',
            reviewed_at=MUSIC_REVIEWED_AT, restriction='noncommercial'),
        _declared('muscriptor_small_f32', 'Muscriptor small F32 package', 'model', 'CC-BY-NC-4.0',
            'https://huggingface.co/audio-cpp/audio.cpp-gguf/blob/' + _MUSCRIPTOR_REVISION + '/README.md',
            'Exact converter table declares noncommercial terms for this package; the repository-wide other tag is not its license. Official MuScriptor raw model card was unavailable in this review (HTTP 401); source-rights conditions remain a separate review.',
            reviewed_at=MUSIC_REVIEWED_AT, restriction='noncommercial'),
        _unknown('native-source-rights', 'Native music source data and compositions', 'source_data', 'Model labels do not clear source training data, compositions, references or artist permissions.'),
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
