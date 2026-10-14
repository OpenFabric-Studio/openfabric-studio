# License and model evidence for artist production

Reviewed **2026-10-14** against OpenFabric Studio commit **`dee6721566f320260d1efc642cbc458167830575`**. This is a read-only evidence audit. No weights were downloaded, no training or inference ran, and no private recording or dataset was sent to a service. Public repository/model-card/API requests fetched documentation and metadata only.

**Decision:** no complete personal-artist production chain is approved by this audit. Some components have commercially permissive published terms and matching installed bytes. The installed YuE2, SheetSage2 and MuScriptor families have published noncommercial restrictions. Singer/source permissions, selected fine-tunes and complete dependency coverage remain separate gates.

## How to read the evidence

| Status | Meaning here |
| --- | --- |
| **Approved component under terms** | The linked component declaration permits commercial use subject to its obligations; where installed bytes were checked, that identity is stated. This is an engineering eligibility classification, not approval of a singer, dataset, output, distribution package or full workflow. |
| **Restricted** | Published terms impose a noncommercial or other material use condition. Commercial artist production stays blocked unless a separate applicable grant is recorded. |
| **Unknown / pending** | Identity, provenance, permission or an applicable dependency term was not established. Unknown commercial permission remains unknown. |

Verified facts are linked declarations, inspected source and observed files/hashes. A publisher's license tag is its declaration, not independent proof that it owns every training recording. A hash proves byte identity, not copyright, consent, quality or safety. Recommendations and interpretations below are explicitly labeled. Absolute local engine/library roots are represented by configuration aliases; no private media names or credentials are included.

Local scope inspected: `AGENTS.md`, `README.md`, `docs/quality-and-provenance.md`, `backend/app/module_licenses.py`, `module_catalog.py`, `module_security.py`, `module_install.py`, `module_evidence.py` and their catalog/evidence/security/install tests; `voice_download.py`, the Seed-VC singing preset/build/compatibility paths, GPT-SoVITS source/configuration, separation setup, `video_engine.py`/its manifest, `desktop/manifest.json`, and legacy model setup scripts. Baseline catalog declarations were reviewed **2026-10-06**. The subsequent uncommitted artist-production changes record the five native model declarations as CC BY-NC 4.0, reviewed **2026-10-14**, with separate source-rights unknowns; the historical installed-byte observations below are unchanged.

## Current code and installation boundaries

OpenFabric code is AGPL-3.0-only, with inherited MIT notices retained in `LICENSE-MIT` and `NOTICE`. The application license is independent of engine/model terms and does not grant rights in source voices or generated media. Distribution and modified network use have corresponding-source obligations described in the [official AGPL text](https://www.gnu.org/licenses/agpl-3.0.html).

The Settings installer uses server-owned source pins and confines managed writes. Existing external/modified trees are preserved. Owned downloads require expected size and SHA-256 before promotion. Origin/loopback protections in `module_security.py` constrain local mutations; they are not license enforcement. License rows and successful setup checks do not certify a local replacement model.

Observed engine checkouts, with read-only `git rev-parse HEAD` and `git status --porcelain`:

| Alias | Observed identity | Local state | Published source declaration |
| --- | --- | --- | --- |
| `$SEED_VC_DIR` | `51383efd921027683c89e5348211d93ff12ac2a8` | Checkout/environment present; modified | [GPL-3.0 source](https://github.com/Plachtaa/seed-vc/blob/51383efd921027683c89e5348211d93ff12ac2a8/LICENSE). Copyleft is not a noncommercial ban. Modified execution files need their own reproducibility record. |
| `$OPENFABRIC_GPT_SOVITS_DIR` | `48b1a0169a28582a8984402f82cf438d3bfa6aca` | Checkout/environment present; clean | [MIT source](https://github.com/RVC-Boss/GPT-SoVITS/blob/48b1a0169a28582a8984402f82cf438d3bfa6aca/LICENSE). |
| `$ACE_STEP_DIR` | `ca1e85fe9430179831e6bc6be790c332190a3866` | Checkout/environment present; modified | [MIT source](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/LICENSE). |
| `$LTX_DIR` | `1724ca673d59f023a8a95efee06e5d36d61c2765` | Checkout/environment present; clean | [MIT MLX integration](https://github.com/dgrauet/ltx-2-mlx/blob/1724ca673d59f023a8a95efee06e5d36d61c2765/LICENSE); this does not override model terms. |
| `$YUE2_DIR` | Packaged engine/model folders present; no Git identity observed | Models enumerated below | Catalog selects audio.cpp `v0.8.1`; [Apache-2.0 engine](https://github.com/0xShug0/audio.cpp/blob/v0.8.1/LICENSE). An observed folder is not proof of the exact executable/source build. |
| `$DEMUCS_DIR` | Installed metadata: Demucs `4.1.0`, MIT License | Environment present | [Upstream MIT declaration](https://github.com/facebookresearch/demucs/blob/e976d93ecc3865e5757426930257e200846a520a/LICENSE). No selected checkpoint identity established in this audit. |

Additional local SHA-256 evidence: Seed-VC `LICENSE` = `8b1ba204bb69a0ade2bfcf65ef294a920f6bb361b317dba43c7ef29d96332b9b`; `inference.py` = `b8ccac4dc66992c8d1ef1a59ec9366c97e500693f2ce9d1693995b8a13761bd4`; `train.py` = `f0dcd6a6faf727419c0e0e955ff69304935f098a9efc9992110ed5850f6b966e`; singing preset `configs/presets/config_dit_mel_seed_uvit_whisper_base_f0_44k.yml` = `9c289dba35c3317e523d505f17337ed1108baae8d8712727d98cd72b8b0e8f7f`. GPT-SoVITS `LICENSE` = `8a064eada186601fc602e45feef61c6abb62246d5ae9c3764dd1fc92d946c3c6`; ACE `LICENSE` = `05a6bce42a62636d2cfb24139cc008b6b899754e244175814bb5dd2f4a485357`; LTX integration `LICENSE` = `b1865d0e24b5adf6c91de99c33067992ecfd3895b152ad50911e2e7747e28778`.

## Singing conversion and training: exact dependency chain

The selected training preset names `DiT_seed_v2_uvit_whisper_base_f0_44k_bigvgan_pruned_ft_ema.pth`, uses **Whisper-small**, BigVGAN 44 kHz and CAMPPlus. The word `base` in the preset/checkpoint filename does not establish that the semantic encoder is Whisper-base. The [pinned training source](https://github.com/Plachtaa/seed-vc/blob/51383efd921027683c89e5348211d93ff12ac2a8/train.py) additionally loads RMVPE, OpenVoiceV2's converter and `se_db.pt`. The [pinned inference source](https://github.com/Plachtaa/seed-vc/blob/51383efd921027683c89e5348211d93ff12ac2a8/inference.py) has different default checkpoint branches; OpenFabric passes selected model/config paths for trained voices.

`voice_download.py` downloads the preset filename from `Plachta/Seed-VC` after an explicit voice build; it does **not** pass a Hub revision. The local cache and fresh hashes below establish today's installed identities, not a guarantee that a future first download uses the same revision. Auxiliary upstream `from_pretrained`/Hub calls can fetch missing dependencies during an authorized model action. Dependency setup alone is not proof of a fully offline first run.

The new strict clean-guide worker sets `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` for its guide analysis/conversion subprocesses. Missing cached dependencies can fail that path; these flags do not establish the exact runtime-loaded inventory or make legacy preparation/training and optional provider workflows offline. Its snapshot deliberately retains `dependency_inventory_complete=false`, so current commercial release remains blocked.

| Component | Dated primary declaration | Commercial component classification | Remaining evidence gap |
| --- | --- | --- | --- |
| Seed-VC baseline and `se_db.pt` | [Published model card at `257283f9…`](https://huggingface.co/Plachta/Seed-VC/blob/257283f9f41585055e8f858fba4fd044e5caed6e/README.md): GPL-3.0 | Approved component under declared terms, with matching installed payloads | GPL obligations for redistribution/derivatives require review; no source-recording/speaker-consent inventory for `se_db.pt` was established. |
| CAMPPlus `campplus_cn_common.bin` | [Publisher model card](https://huggingface.co/funasr/campplus), revision `e4b6ede7ce16997aff4ae69fbca1f0175e2afede`: Apache-2.0 | Approved component under terms; installed payload matches | Does not clear reference recordings or make embeddings identity verdicts. |
| RMVPE `rmvpe.pt` | [Exact downloaded repository](https://huggingface.co/lj1995/VoiceConversionWebUI), revision `e6d0c1a17da07c33557852f9dfa2bd44cc75737d`: MIT tag; [original RMVPE code](https://github.com/Dream-High/RMVPE): Apache-2.0 | Published commercial-compatible declaration; provenance review pending | Exact repository payload matches, but its model card has no substantive provenance. Original code license alone does not attest this republished checkpoint's training rights. |
| OpenVoiceV2 converter | [Publisher card](https://huggingface.co/myshell-ai/OpenVoiceV2), revision `f36e7edfe1684461a8343844af60babc2efbb727`: MIT, explicitly allows commercial use | Approved component under terms; installed payload matches | Neither reference-speaker permission nor personal-artist quality established. |
| BigVGAN 44 kHz / 128-band / 512x | [NVIDIA card](https://huggingface.co/nvidia/bigvgan_v2_44khz_128band_512x), revision `95a9d1dcb12906c03edd938d77b9333d6ded7dfb`: MIT | Approved component under terms; installed payload matches | Publisher describes a large training compilation; this audit does not independently clear that corpus. |
| Whisper-small semantic encoder | [Exact Transformers card](https://huggingface.co/openai/whisper-small/blob/973afd24965f72e36ca33b3055d56a652f456b4d/README.md): Apache-2.0 | Approved component under declared terms; installed payload matches | [Original Whisper repository](https://github.com/openai/whisper) declares code/weights MIT. Record both declarations for this converted package rather than assume all formats share one tag. |
| Local artist fine-tune, target reference and selected singing recordings | Local application retains original/selected material and model signatures | **Unknown / pending for commercial use** | Model terms, recording/master/composition permission and singer confirmation must attach to the exact artist model and use. No private consent documents or recordings were inspected. |

Fresh locally computed SHA-256 values, with unchanged size/mtime during hashing. All seven payloads match the publisher's LFS SHA-256 metadata at the listed revision. Paths are under `$SEED_VC_DIR/checkpoints/models--OWNER--REPO/snapshots/REVISION/`; BigVGAN and Whisper also appear under `checkpoints/hf_cache/` with identical hashes.

| Model repository / snapshot revision | Relative payload | Bytes | Local SHA-256 |
| --- | --- | ---: | --- |
| `Plachta/Seed-VC` / `257283f9f41585055e8f858fba4fd044e5caed6e` | `DiT_seed_v2_uvit_whisper_base_f0_44k_bigvgan_pruned_ft_ema.pth` | 820864573 | `883d19be9fe9c4bb2dce30d545cd77e5ed2b6326c7ed1c4d9a7ce8e555b163f9` |
| Same | `se_db.pt` | 102405286 | `a76e1ad4fac609cfa1a5198bf789427c5e7822d656fab1ffed6cd80fc0d381b7` |
| `funasr/campplus` / `e4b6ede7ce16997aff4ae69fbca1f0175e2afede` | `campplus_cn_common.bin` | 28036335 | `3388cf5fd3493c9ac9c69851d8e7a8badcfb4f3dc631020c4961371646d5ada8` |
| `lj1995/VoiceConversionWebUI` / `e6d0c1a17da07c33557852f9dfa2bd44cc75737d` | `rmvpe.pt` | 181184272 | `6d62215f4306e3ca278246188607209f09af3dc77ed4232efdd069798c4ec193` |
| `myshell-ai/OpenVoiceV2` / `f36e7edfe1684461a8343844af60babc2efbb727` | `converter/checkpoint.pth` | 131320490 | `9652c27e92b6b2a91632590ac9962ef7ae2b712e5c5b7f4c34ec55ee2b37ab9e` |
| `nvidia/bigvgan_v2_44khz_128band_512x` / `95a9d1dcb12906c03edd938d77b9333d6ded7dfb` | `bigvgan_generator.pt` | 489041291 | `d9fe7ec6bd0b44ed9d66973d5012d8181c1570b01e5c72df51973e241dccd357` |
| `openai/whisper-small` / `973afd24965f72e36ca33b3055d56a652f456b4d` | `model.safetensors` | 966995080 | `1d7734884874f1a1513ed9aa760a4f8e97aaa02fd6d93a3a85d27b2ae9ca596b` |

## Native music and transcription: restricted weights

**Verified baseline discrepancy, corrected in the working tree:** the catalog at the audited commit labeled each selected native package's model terms unknown. The uncommitted artist-production changes now record their CC BY-NC 4.0 declarations and commercial restriction notes. The exact installed YuE2 revision's Hub metadata declares **CC BY-NC 4.0**. The [current converter card](https://huggingface.co/audio-cpp/Yue2-3B-GGUF) and [official YuE2-3B card](https://huggingface.co/m-a-p/YuE2-3B) agree; the converter identifies source revision `1a96eca688d6ae5d7f0feb88573fec89920fcd19`. Apache-2.0 audio.cpp does not remove this restriction.

The [SheetSage2 converter card](https://huggingface.co/audio-cpp/SheetSage2-GGUF) and [official SheetSage2 card](https://huggingface.co/m-a-p/SheetSage2) declare CC BY-NC 4.0. The conversion merges the MERT-v2-FullSong backbone and points to SheetSage2 source revision `eab522a8168e8b8b8c4856bf8609cd86198f01fe`; that bundled backbone is another artifact whose terms matter.

The [mixed converter repository](https://huggingface.co/audio-cpp/audio.cpp-gguf) lists **MuScriptor-Small-GGUF as CC BY-NC 4.0**, although the repository-wide tag is `other`. The [official MuScriptor-small card](https://huggingface.co/MuScriptor/muscriptor-small) has the same license plus explicit source-rights/use conditions. A multi-model repository's top-level tag cannot clear every file.

**Recommendation:** classify all five native packages below as **Restricted** for commercial artist production until documented separate permission applies. Under the [official CC BY-NC 4.0 terms](https://creativecommons.org/licenses/by-nc/4.0/legalcode.en), the relevant grant is limited to noncommercial purposes. A paid artist workflow does not become noncommercial because inference happens on a laptop. No finding here declares ownership or blanket output-license inheritance; the practical gate concerns use of restricted models in commercial production.

Fresh hashes under `$YUE2_DIR/models/`:

| Package | Relative payload | Bytes | Local SHA-256 | Identity evidence |
| --- | --- | ---: | --- | --- |
| `yue2_main_q8_0` | `Yue2-3B-GGUF/yue2-3b-q8_0.gguf` | 4264199840 | `1bbbfeea90c6219afb0c73b6a212ce7847ca6a982534388064f8e61e031b6a3f` | Matches publisher metadata at installed receipt revision `eb14a51700bf8baac825a690b2f3b24a01239f85`. |
| `yue2_main_q4_0` | `Yue2-3B-GGUF/yue2-3b-q4_0.gguf` | 2665645728 | `cb1f76c8c90755a4c5c9cc0220b26b914570d74f4dc3ec6599469a46d7990f4b` | Same revision, publisher match. |
| `yue2_vae_f16` | `Yue2-3B-GGUF/yue2-vae-f16.gguf` | 265218656 | `d4f4a05d8f291ae820cd1e43609da3fa91b56465810091a2b08c3350b751719d` | Same revision, publisher match. |
| `sheetsage2_orig` | `SheetSage2-GGUF/sheetsage2-orig.gguf` | 2708224512 | `52bb5846c452037d39931aa8050885b6c751b9c7afcc8ef6d6d3067d241731a4` | Matches a hash stated in converter README. Local receipt's resolved revision is `main`; exact historical immutable package revision **unknown**. Current API weights have a different hash/size. |
| `muscriptor_small_f32` | `MuScriptor-Small-GGUF/muscriptor-small-f32.gguf` | 411908672 | `4bff6f1c45b445e144c176434fbe27614ad6c4686e03f99ee11b17e15163fba2` | Matches converter API payload at `f861648c0c1f4da9f18e9a0ce5e217b0679bb2f5`. Local receipt records `main`; original installed revision **unknown**. |

Old package receipts contain ETags that differ from freshly computed payload SHA-256. They were not treated as checksum proof. The later reliable downloader requires immutable identities/digests for new owned transfers; its source guarantees do not retroactively certify these older receipts. No files or receipts were repaired or replaced.

## ACE-Step: existing music engine, separate artist-synthesis research

The existing integration is music generation/cover/edit support. **A personal singing-identity synthesis workflow is a separate research proposal and stays disabled by default; this audit adds no integration, flag or training job.**

[ACE-Step's source declaration](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/LICENSE) and [published bundle](https://huggingface.co/ACE-Step/Ace-Step1.5) are MIT. Individually checked [XL base](https://huggingface.co/ACE-Step/acestep-v15-xl-base), [XL SFT](https://huggingface.co/ACE-Step/acestep-v15-xl-sft) and [4B LM](https://huggingface.co/ACE-Step/acestep-5Hz-lm-4B) also declare MIT. [Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) declares Apache-2.0; ACE's bundle MIT tag does not erase that source term. These are approved component candidates under terms. No audited training-corpus ledger, personal-singer consent or held-out identity/lyric/melody result was established.

All following installed `*.safetensors` were freshly hashed and match public publisher metadata. Paths are under `$ACE_STEP_DIR/checkpoints/`. Their matching revisions are: bundle **`19671f406d603126926c1b7e2adc169acbcade22`**, XL base **`220c1166efbdd9583eafcb12eb160594bbfcb241`**, XL SFT **`d06de46b4622f781cf07f4a013a67d591ca52819`**, 4B LM **`0a3ec94b557aea7d508da38b31cfe7341f6ff737`**. Qwen's own matching publisher revision is **`97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`**. These matches are observations, not installation pins or proof that a running service loaded them.

| Relative payload | Bytes | Local SHA-256 |
| --- | ---: | --- |
| `Qwen3-Embedding-0.6B/model.safetensors` | 1191586416 | `0437e45c94563b09e13cb7a64478fc406947a93cb34a7e05870fc8dcd48e23fd` |
| `acestep-5Hz-lm-1.7B/model.safetensors` | 3708521528 | `f161689da73e5ecefa28ff780d51c2d92a00f056d021d7933c779ed5c6cd7db8` |
| `acestep-5Hz-lm-4B/model-00001-of-00002.safetensors` | 4999914896 | `ada9d0d4ff48f112de3f7b82cd4e7d57b4245932657e8b8edc9a5ded6a23b77f` |
| `acestep-5Hz-lm-4B/model-00002-of-00002.safetensors` | 3379239240 | `6302100c3577e2f1dbf32573e9b5e6e6b1bea7af101b433c2d3d6280faa8ab68` |
| `acestep-v15-turbo/model.safetensors` | 4787825604 | `3f6e0797fad420a39bd33979eb6e840e30989e34a3794e843d23b60ec6e422d7` |
| `acestep-v15-xl-base/model-00001-of-00004.safetensors` | 4986971456 | `da654de2a66ace691fb06fedca81f14d20451eecd534c04a69d2f0207303ef23` |
| `acestep-v15-xl-base/model-00002-of-00004.safetensors` | 4986942776 | `ade70e18afcf9e3b656cda04e15b52d1a480f4d6a0e40141f3e614a4103a279a` |
| `acestep-v15-xl-base/model-00003-of-00004.safetensors` | 4986942808 | `e44b5ec5fa00bb5a0da423355772bfb3688d1330d70ba65cfd5611eebb20f9d5` |
| `acestep-v15-xl-base/model-00004-of-00004.safetensors` | 4988483464 | `3994ec426e1fb1cc92488b3fffa8bbbf86e8daa83ab23a4dc11eeb0ad12db52b` |
| `acestep-v15-xl-sft/model-00001-of-00004.safetensors` | 4986971456 | `1d304b8a34859cd92c349c71ae6c27d50564844c7f7611d2d32913f67508e5a4` |
| `acestep-v15-xl-sft/model-00002-of-00004.safetensors` | 4986942776 | `94e33648f514fdd00f473f2d2680af959204b58b15f7ab37246ac16d066aac87` |
| `acestep-v15-xl-sft/model-00003-of-00004.safetensors` | 4986942808 | `befe558365f53bc6c44f3f7e521cb2dd026927a817c36aa05ad94fcd80a533a9` |
| `acestep-v15-xl-sft/model-00004-of-00004.safetensors` | 4988483464 | `dfbc77aa26e54d0127892d6e7f3c0868bfc4623aa067867f6595c3b2fb7d31a4` |
| `vae/diffusion_pytorch_model.safetensors` | 337431388 | `da17edb604c40deaf09e9b24974e590d1ca83a374070e5d0884cfa4bed9a99b0` |

Model sidecar configuration, tokenizer files and `silence_latent.pt` were not exhaustively hashed. The current installer invokes upstream `acestep-download`; third-party downloads and first-use behavior are separate from OpenFabric's bounded artifact downloader. Legacy `setup_models.sh`/`.ps1` explicitly leave ACE checkpoints to first-request loading. Do not describe the source installer as a fully pinned offline model pack.

## Speech, video, screening and separation

| Area | Verified component evidence | Production status / unresolved scope |
| --- | --- | --- |
| GPT-SoVITS base speech | MIT source; [publisher model repository](https://huggingface.co/lj1995/GPT-SoVITS) revision `8f8857563c040d542de108a2c18b0425abd6c9c5` declares MIT. All 18 locally observed pretrained weight files matched that repository's SHA-256 metadata. | Approved published-component candidates under terms. API does not attest its loaded checkpoint; observed disk files do not identify a running renderer or arbitrary fine-tune. Full per-auxiliary provenance still pending. |
| GPT-SoVITS Chinese encoders | [Tencent Chinese HuBERT](https://huggingface.co/TencentGameMate/chinese-hubert-base) declares MIT; [HFL Chinese RoBERTa](https://huggingface.co/hfl/chinese-roberta-wwm-ext-large) declares Apache-2.0. Installed files match GPT publisher payloads. | Keep encoder terms separate from the GPT umbrella declaration. ERes2NetV2 checkpoint, version-specific vocoders, language dictionaries/G2P models and additional WebUI ASR/separation tools need their own complete source/term review before extending the approved scope. |
| Bundled speech references | [VCTK 0.92 original deposit](https://datashare.ed.ac.uk/handle/10283/3443) and local starter catalog declare CC BY 4.0; local docs retain transcripts/attribution. | Source redistribution requires attribution. This does not turn a starter voice into a personal singer authorization or a promised endorsement. Private uploaded references remain unknown until their own permissions are recorded. |
| Demucs | Installed package `4.1.0`, MIT metadata. Upstream [baseline mapping](https://github.com/facebookresearch/demucs/blob/e976d93ecc3865e5757426930257e200846a520a/demucs/remote/htdemucs.yaml) selects `955717e8`; [high-quality mapping](https://github.com/facebookresearch/demucs/blob/e976d93ecc3865e5757426930257e200846a520a/demucs/remote/htdemucs_ft.yaml) selects `f7e0c4bc`, `d12395a8`, `92cfc3b6`, `04573f0d`. | Code declaration verified. No checkpoint found at inspected default/managed Torch cache paths; alternate cache locations were not exhaustively searched. Selected weight identity/terms remain **unknown** in this report, and source music rights remain separate. |
| Optional Kimberley MelBand RoFormer | Setup pins [ZFTurbo source](https://github.com/ZFTurbo/Music-Source-Separation-Training/blob/84b1eac0887756b4f1a9d7a1ff49105939749ed2/LICENSE) `84b1eac0887756b4f1a9d7a1ff49105939749ed2` (MIT), [model](https://huggingface.co/KimberleyJSN/melbandroformer) `ac9b0614ab3cd7f77219e18ba494dfd93956c348` (MIT tag). Expected checkpoint 913106900 bytes / SHA-256 `87201f4d31afb5bc79993230fc49446918425574db48c01c405e44f365c7559e`. | Configured engine/config/checkpoint paths were missing. Expected hash is **not a local observation**. Publisher card lacks substantive training provenance; do not classify all RoFormer variants by this model's tag. |
| Optional ECAPA speaker screening | [SpeechBrain 1.0.3 source](https://github.com/speechbrain/speechbrain/blob/v1.0.3/LICENSE) and [ECAPA VoxCeleb checkpoint](https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb), revision `0f99f2d0ebe89ac095bcc5903c4dd8f72b367286`, declare Apache-2.0. App expects checkpoint SHA-256 `0575cb64845e6b9a10db9bcb74d5ac32b326b8dc90352671d345e2ee3d0126a2`. | No weight path configured in observed environment. This expected digest was not locally verified. VoxCeleb source data and private screening references retain separate terms. Similarity cannot certify consent, identity or artistic quality. |
| LTX 2.3 converted weights | Manifest pins [MLX conversion](https://huggingface.co/dgrauet/ltx-2.3-mlx-q8) `6671a7572a530862d1d60ce393b5d93491e3f76b`; all 14 expected files existed under `$DATA_DIR/models/ltx/hub/…/snapshots/REVISION/`. | **Restricted/custom terms**: [LTX-2 Community License](https://huggingface.co/Lightricks/LTX-2.3/blob/main/LICENSE) imposes acceptable-use and redistribution/derivative conditions; entities at least USD 10m annual revenue, aggregated with controlled affiliates, require a paid license. No user/entity eligibility or local large-weight rehash performed. |
| LTX text encoder | Manifest pins [Gemma 3 12B MLX 4-bit](https://huggingface.co/mlx-community/gemma-3-12b-it-4bit) `86cc6a8dedbc456dd0e4af01a9d09f396f77e558`; all 13 expected files existed. | **Restricted/custom terms**: [Gemma agreement](https://ai.google.dev/gemma/terms) applies independently, including prohibited-use and redistribution/notice conditions. Local large payloads not freshly rehashed. |
| LTX 2.5 comparison | Manifest names `dgrauet/ltx-2.5-mlx-q8`, revision `746ca9aacb697d2c739f544d68b584214dedcc75`. None of 20 expected files existed at the configured cache. | **Unknown / pending**, opt-in comparison, no installation/quality/license claim added. |
| Optional Kokoro | Catalog source pin `dfb907a02bba8152ca444717ca5d78747ccb4bec`; [official 82M model card](https://huggingface.co/hexgrad/Kokoro-82M) declares Apache-2.0, published v1.0 SHA-256 `496dba118d1a58f5f3db2efc88dbdc216e0483fc89fe6e47ee1f2c53f18ad1e4`. | Published component candidate; no local payload/voice preset clearance in this audit. espeak-ng and local presets have separate terms. Kokoro is speech synthesis, not a personal singing clone. |
| Optional Chatterbox / Wan / RVC | [Chatterbox publisher repository](https://huggingface.co/ResembleAI/chatterbox) declares MIT; [Wan TI2V-5B](https://huggingface.co/Wan-AI/Wan2.2-TI2V-5B) declares Apache-2.0; [pinned RVC source](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI/blob/81eed5e8f68b6bed1789f682fe78cdd324495afc/LICENSE) declares MIT. Setup does not download these weights. | Exact selected local checkpoint/conversion/voice remains **unknown / pending**. Chatterbox original/multilingual paths must be matched to actual selected artifacts. No automatic approval of trained RVC voices, HuBERT or a Wan conversion. |

Representative GPT-SoVITS locally computed hashes, under `$OPENFABRIC_GPT_SOVITS_DIR/GPT_SoVITS/pretrained_models/` (all match the publisher revision above):

| Relative payload | Bytes | Local SHA-256 |
| --- | ---: | --- |
| `s2G488k.pth` | 105973721 | `020a014e1e01e550e510f2f61fae5e5f5b6aab40f15c22f1f12f724df507e835` |
| `s1bert25hz-2kh-longer-epoch=68e-step=50232.ckpt` | 155093966 | `b1c1e17e9c99547a89388f72048cd6e1b41b5a18b170e86a46dfde0324d63eb1` |
| `s1v3.ckpt` | 155284856 | `87133414860ea14ff6620c483a3db5ed07b44be42e2c3fcdad65523a729a745a` |
| `gsv-v2final-pretrained/s2G2333k.pth` | 106035259 | `924fdccaa3c574bf139c25c9759aa1ed3b3f99e19a7c529ee996c2bc17663695` |
| `s2Gv3.pth` | 769035145 | `f33abb1920076d988e1711d5f41b5c9c6d7f92575b4acf0ad4fae6a4ebf0cf19` |
| `gsv-v4-pretrained/s2Gv4.pth` | 769025545 | `906fe22f48c3e037a389df291d4d32a9414e15dbb8f9628643e83aaced109ea4` |
| `gsv-v4-pretrained/vocoder.pth` | 57781109 | `4d611913df7b12d49e8976c944558d2d096816365edfc6c35a9e85b67dd14ed9` |
| `v2Pro/s2Gv2Pro.pth` | 162303657 | `0f8ead815234365edf045c6d86370ed6e4f440e8195be77ff0ea72684ad406a5` |
| `v2Pro/s2Gv2ProPlus.pth` | 200125741 | `d42a22bbbf65fb2bbdd45ad6a66841156977db45c7aabe0a6992ff378d9c7d3b` |
| `sv/pretrained_eres2netv2w24s4ep4.ckpt` | 107528697 | `4f5a0bf73c61eb41b174e1bb54e7ee3c83233892be8e0af1f187024e8e581a35` |
| `chinese-hubert-base/pytorch_model.bin` | 188811417 | `24164f129c66499d1346e2aa55f183250c223161ec2770c0da3d3b08cf432d3c` |
| `chinese-roberta-wwm-ext-large/pytorch_model.bin` | 651225145 | `e53a693acc59ace251d143d068096ae0d7b79e4b1b503fa84c9dcf576448c1d8` |
| `models--nvidia--bigvgan_v2_24khz_100band_256x/bigvgan_generator.pt` | 450088331 | `6f9c5715550c9d0f11159ceb8935638da5aeb19e27d1e63677632df095e376f5` |

## Media and import tools

Observed `$PATH/ffmpeg` is **9.0.1**, built with `--enable-gpl --enable-version3` and shared libraries, among other options. Under [FFmpeg's official licensing guidance](https://ffmpeg.org/legal.html), this is not a blanket LGPL-only build. Binary SHA-256: `9f5ddd4f8b36eb07b0c5d60ab6ea1e618bedd70bc45feab82b4dc23be1596ad1` (426496 bytes); `$PATH/ffprobe`: `0ecd5e1affb1466b129b4f65bee261e9ab625c4d4a2e7c3e9089f63d44808605` (253488 bytes). These executable hashes do not cover their dynamically linked libraries or establish redistribution compliance. Commercial use is not forbidden merely by GPL; binary distribution must satisfy applicable licenses.

Catalog declarations for whisper.cpp (MIT), yt-dlp (Unlicense), Deno (MIT) and Calibre (GPL-3.0) are tool/source declarations. The configured `REFERENCE_WHISPER_MODEL` was absent. No checkpoint/conversion was inferred. Downloaded songs, transcripts, book text, covers and third-party images remain **unknown / pending** until their own rights are recorded. Access to a download tool is not a recording, narration or training license.

## Disabled-by-default synthesis research evidence

No DiffSinger module, checkpoint or production route is established by the inspected OpenFabric catalog/install paths. Candidate research remains disabled; these links are evidence for a future scoped decision, not an implementation plan approved for execution.

| Candidate | Primary evidence reviewed 2026-10-14 | What it establishes | What it does not establish |
| --- | --- | --- | --- |
| Original DiffSinger | [Official repository](https://github.com/MoonInTheRiver/DiffSinger), reviewed HEAD `4662c53a27a5ac662821eae23a7d71cfcff7356d`, [MIT license](https://github.com/MoonInTheRiver/DiffSinger/blob/4662c53a27a5ac662821eae23a7d71cfcff7356d/LICENSE) | An official lyric/F0 or lyric/MIDI-to-singing research implementation; separate vocoder/data pipeline. | No selected commercial-cleared singer bank, auxiliary weight set, dataset or local performance result. Old CUDA/Python examples are not Apple Silicon verification. |
| OpenVPI DiffSinger | [Maintained fork](https://github.com/openvpi/DiffSinger), reviewed HEAD `ba031f6f2f969373b49a926cf8b007e9ae48834f`, [Apache-2.0 license](https://github.com/openvpi/DiffSinger/blob/ba031f6f2f969373b49a926cf8b007e9ae48834f/LICENSE) | Code candidate with explicit pitch/energy/breathiness controls and linked dataset/production tooling; README requires voice consent. | Code license does not license downloaded voicebanks, NSF/HiFi-GAN/other vocoders, pitch extractors, phoneme resources or the artist's recordings. Each exact artifact remains unknown until selected and reviewed. |
| ACE-Step artist synthesis experiments | Existing pinned integration and MIT model evidence above; [upstream current documentation](https://github.com/ace-step/ACE-Step-1.5) describes generation and adaptation capabilities | Plausible research reuse of the installed engine, with comparatively clear component license declarations. | No proven stable personal voice, note/phoneme timing control, lyric intelligibility or clean held-out singer evaluation in this audit. Capability claims and leaderboard marketing are not evidence for this artist. |

**Inference/recommendation:** score-controlled singing synthesis and timbre conversion solve different problems. Preserve Seed-VC as the current conversion baseline; compare a separate controlled synthesis experiment only after choosing exact weights, auxiliary terms, singer-authorized data and held-out material. Replacing a known pipeline with a paper title is an efficient way to acquire more weekends of debugging.

## Commercial release gate and unresolved work

Before any artist production workflow is marked approved, attach a manifest for the exact engine source (including patches), main checkpoint, auxiliaries, adapters and output transformations; include immutable revisions/hashes and applicable terms. Record source recording/master/composition permissions, intended uses, singer confirmation, and held-out exclusions independently of model setup. Record any separate noncommercial-model grant against the exact covered use; no such grant was observed here. Resolve entity eligibility and downstream notice/source obligations for custom/copyleft components before distribution or hosting.

The evidence collection used read-only filesystem checks, bounded public metadata/documentation fetches, `git` identity/status, installed package metadata and fresh SHA-256 hashing. Public payload comparisons used Hugging Face's `https://huggingface.co/api/models/OWNER/REPO/revision/REVISION?blobs=true` responses on the review date; returned LFS SHA-256 values were compared with freshly hashed local files. Some immutable HTML/raw pages were unavailable through the browser tool; exact-revision API metadata remained accessible. It did not load untrusted `.pth`/`.pt` payloads, call a model API, start an engine, inspect private assets, accept gated model terms, install dependencies or change model/configuration files. Test source was inspected; no test/build result is claimed by this document.

Limitations: this is not a complete transitive software/model SBOM or a legal clearance opinion. LTX/Gemma large files were checked for presence against the existing manifest, not freshly hashed. Alternate Demucs caches, all language-specific speech resources, future downloads, private fine-tunes/permissions, cloud provider contracts and live loaded-model identities remain unverified. GPU quality, singer identity, pronunciation, timing and production readiness require separate measured evidence.
