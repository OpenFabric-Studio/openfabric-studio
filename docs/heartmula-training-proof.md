# HeartMuLa training boundary and Mac proof

Recorded 2026-10-15 on an Apple M4 Max with 128 GiB unified memory. This is a functional short-run proof, not a musical-quality evaluation or NVIDIA result. Original installed model files were read without modification.

## Reviewed sources

- SimpleTuner `a0e7ffadfcd0d3d0f68b400f9697c464da6f9408`, AGPL-3.0-or-later. Its package requires Python `>=3.12,<3.14`, Torch `>=2.13.0` and Transformers `>=5.6.0`. The worker reuses its HeartMuLa model classes; the complete SimpleTuner CLI is not installed.
- Official HeartLib `5858ca8f1ffe58d62be7c007ea55f1033c5ca456`, Apache-2.0 with an MIT MusicFM notice in its feature-extractor source. Its official encoder uses the decoder's actual RVQ, not a mel-spectrogram approximation.
- HeartMuLa 3B weights `d12ac79c6b3387d5c9eee456323495d8f08bb09d`; HeartCodec decoder `f889dab0532cfa4bf459f2a3367eb6d346b8eeda`; encoder `e6020510612d6653b73d6cd54393ef80929e3c81`. Published model declarations are Apache-2.0. Recording and adapter-distribution rights are separate.
- Existing MLX port `02dd2e22f6a1bf1a2f769a13f8af952a7a4e9cee` was inspected and compared, but adapter generation uses the separately corrected HF worker.

The model fingerprint is SHA256 of compact sorted JSON mapping `config.json` and the four safetensors filenames to their actual SHA256 values. For the reviewed publisher files it is `852e5728588705c4722f3a562265626c1324632060eb4cfb541bbecb22514509`. The worker recomputes it; imports reject a different identity. Tokenizer SHA256 is `79e3e522635f3171300913bb421464a87de6222182a0570b9b2ccba2a964b2b4`.

## Compatibility findings

The pinned SimpleTuner importer renames original torchtune keys without converting Q/K row order. HF uses separate real/imaginary halves; original torchtune and the MLX port use adjacent pairs. In addition, original torchtune 0.4.0 uses an 8192-position rotary frequency reference for both stacks. SimpleTuner substitutes 2048 for its local decoder. The MLX port omits Llama3 frequency scaling entirely.

A fixed-seed FP32 comparison used actual torchtune 0.4.0 attention, feed-forward, RMS normalization and transformer classes, and actual HF Llama classes. The tiny model had two layers, hidden width 32, 4 query/2 KV heads, intermediate width 256 and input shape `[1,128,32]`.

| Stack | Rename only, maximum absolute hidden error | Q/K rows corrected | Rows and rotary reference corrected | Corrected common linear-head logit error |
| --- | ---: | ---: | ---: | ---: |
| Backbone | 0.1586950 | 4.768e-7 | 4.768e-7 | 2.980e-7 |
| Local decoder | 0.1328278 | 0.0011610 | 4.768e-7 | 2.384e-7 |

The actual MLX tiny backbone differed by 0.0035468 before restoring original scaled frequencies and by 5.960e-7 afterwards. These results justify the two HF corrections; they do not establish a full MLX adapter loader.

Original generation also replaces the tag/lyrics separator with `muq_linear(zero_512)`. The HF worker explicitly restores that conditioning for training and generation. It removes both unused HF token-embedding tables and loads the remaining checkpoint strictly.

The official codec's timestep helper calls `.type(timesteps.type())`, which fails with `torch.mps.FloatTensor`. The worker supplies the same timestep equation with `.to(dtype=timesteps.dtype)` in its process. CPU output matched exactly; a four-value MPS check had maximum absolute difference `5.481e-5`. Official decoding retains the 29.76-second internal chunk size and trims to token duration; setting a five-second chunk would produce a zero hop.

## Actual short run

The dataset was a six-second synthetic tone clip, not a personal recording or quality benchmark.

1. Official FP32 encoder plus matching decoder/RVQ loaded and encoded on MPS in 10.69 seconds. It produced CPU int64 tokens `[8,76]`, range 16–8186, within the 8192-code RVQ vocabulary.
2. The corrected complete 3B model ran two fresh AdamW steps on MPS, rank 8, alpha 8, learning rate 0.0001, batch 1. Final loss was finite, `3.1514685`; adapter tensors changed. The encode/load/train worker completed in approximately 22 seconds in that run.
3. The resulting standard PEFT adapter contained 434 factors: 217 complete projection pairs across 28 backbone and 3 decoder layers. Size: 53,995,288 bytes; SHA256: `678fa94b2bc764649311e57efe3ad25b0d35eb73bb29e68b34d09b8cf08cb9a4`. It passed the application's actual safetensors/HeartMuLa artifact validator.
4. A fresh single generation command reloaded that adapter at strength 1, generated 63 valid frames and decoded them through the official codec on MPS. The saved result was finite stereo WAV, 48 kHz, 241,920 frames, 5.04 seconds. Base hashing/loading plus token generation took 52.904 seconds in that later run. Driver allocation sampled after generation was 8,391,671,808 bytes (7.82 GiB); this is not a measured peak.

No listening assessment, held-out improvement, full-state resume, long-duration stress test or CUDA execution is claimed. Training, generation and codec operations run as owned subprocesses; outer job supervision owns cancellation and descendants.

The final snapshot boundary also keeps the selected float32 audio in the run before encoding, checks the original hash before/after reading, and records original duration, offset, private-audio hash and token hash. CPU regressions verify preservation and changing-source rejection. Vocal inputs longer than the selected segment are rejected when they carry lyrics; lyrics must describe that already-trimmed clip.

## Reproducible checks

Backend unit tests use temporary configuration, library, Seed-VC and module roots. The optional worker tests run in a prepared codec environment, use CPU tensors and temporary audio, and download/load no model.

The strict worker check uses actual Torch and pinned upstream source, rather than the core's deliberately small legacy Torch declaration:

```sh
backend/.venv/bin/python backend/scripts/check_heart_lora_types.py \
  --python /path/to/trainer-venv/bin/python \
  --simpletuner /path/to/simpletuner \
  --heartlib /path/to/heartlib
```

Pinned runtime requirements are in `backend/app/assets/music-training-heart-{trainer,codec}-requirements.txt`. Both prepared environments passed dependency checks; the trainer reference-only torchtune package was removed before final verification. The codec's Transformers 4.57.0 matches official HeartLib's declared pin; PyPI marks that release yanked for its setup metadata. The isolated installation and codec execution were verified here.

Primary source: [SimpleTuner model](https://github.com/bghira/SimpleTuner/blob/a0e7ffadfcd0d3d0f68b400f9697c464da6f9408/simpletuner/helpers/models/heartmula/modeling_heartmula.py), [official HeartMuLa](https://github.com/HeartMuLa/heartlib/blob/5858ca8f1ffe58d62be7c007ea55f1033c5ca456/src/heartlib/heartmula/modeling_heartmula.py), [official codec](https://github.com/HeartMuLa/heartlib/blob/5858ca8f1ffe58d62be7c007ea55f1033c5ca456/src/heartlib/heartcodec/modeling_heartcodec.py), [encoder release](https://huggingface.co/HeartMuLa/HeartCodec-oss-encoder/tree/e6020510612d6653b73d6cd54393ef80929e3c81).
