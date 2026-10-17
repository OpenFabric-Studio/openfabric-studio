// Generated from temporary synthetic 3s stereo CPU fixtures; no model inference.
export const measuredVoiceMixFixture: unknown = {
  "schema_version": 1,
  "id": "557e92f6e721414c996db94fe7b4d1bf",
  "audio_version_id": "20a994cfe1aa4cf2ab654c38fec2de87",
  "source": {
    "kind": "version",
    "track_id": 1,
    "version_id": "1653cfb24a85461ab95fee53e7e4b529"
  },
  "source_sha256": "f68cf8b65a97c9d3c3246a16543f525b662074e1c8aed7c4ad25d1eebc8cec38",
  "settings": {
    "schema_version": 1,
    "vocal_level": "match_original",
    "manual_gain_db": 0.0,
    "eq_bands": [],
    "compression": null,
    "loudness": {
      "profile": "off",
      "integrated_lufs": -16.0,
      "true_peak_dbtp": -2.0
    }
  },
  "conversion": {
    "voice_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "model_revision_id": "fixture",
    "files": [
      {
        "role": "checkpoint",
        "name": "checkpoint",
        "sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "authentication": "observed_local_bytes"
      },
      {
        "role": "configuration",
        "name": "configuration",
        "sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "authentication": "observed_local_bytes"
      },
      {
        "role": "voice_reference",
        "name": "voice_reference",
        "sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "authentication": "observed_local_bytes"
      }
    ],
    "engine": "seed-vc",
    "engine_commit": "unknown",
    "software_version": "unknown",
    "diffusion_steps": 30,
    "inference_cfg_rate": 0.7,
    "f0_condition": true,
    "auto_f0_adjust": false,
    "seed": null,
    "dependency_inventory_complete": false,
    "bitwise_determinism_verified": false,
    "training_steps": 0,
    "baseline_recorded_sha256": null,
    "selection_fingerprint": null,
    "preparation_revision": null
  },
  "reference_metrics": {
    "duration_sec": 3.0,
    "integrated_lufs": -20.75,
    "loudness_range_lu": 0.0,
    "true_peak_dbtp": -20.0,
    "sample_peak_dbfs": -19.99999987057016,
    "full_scale_fraction": 0.0,
    "near_full_scale_fraction": 0.0,
    "measurement_method": "ffmpeg-loudnorm-oversampled-v1",
    "ffmpeg_version": "ffmpeg version 9.0.1 Copyright (c) 2000-2026 the FFmpeg developers",
    "source_sha256": "f68cf8b65a97c9d3c3246a16543f525b662074e1c8aed7c4ad25d1eebc8cec38",
    "samples_analyzed": 288000,
    "warnings": []
  },
  "converted_metrics": {
    "duration_sec": 3.0,
    "integrated_lufs": -26.75,
    "loudness_range_lu": 0.0,
    "true_peak_dbtp": -26.02,
    "sample_peak_dbfs": -26.020599783849786,
    "full_scale_fraction": 0.0,
    "near_full_scale_fraction": 0.0,
    "measurement_method": "ffmpeg-loudnorm-oversampled-v1",
    "ffmpeg_version": "ffmpeg version 9.0.1 Copyright (c) 2000-2026 the FFmpeg developers",
    "source_sha256": "1007b218b589d7b6a2bdb567428bce612744b5f76dd95ec1f56eaf4f9c33f9ca",
    "samples_analyzed": 288000,
    "warnings": []
  },
  "applied_gain_db": 6.0,
  "residual_lu": 0.0,
  "warnings": [],
  "transform": {
    "schema_version": 1,
    "id": "43b7c694f33c4a05a3893709d2c9e049",
    "revision": 0,
    "artifact_name": "43b7c694f33c4a05a3893709d2c9e049.wav",
    "audio_sha256": "29a639a5230f2a3301e26d6b9984ee57a7928eeefa2e1998a45b47e15cd40c09",
    "sample_rate_hz": 48000,
    "channels": 2,
    "duration_frames": 144000,
    "output_metrics": {
      "duration_sec": 3.0,
      "integrated_lufs": -20.75,
      "loudness_range_lu": 0.0,
      "true_peak_dbtp": -20.02,
      "sample_peak_dbfs": -20.02059974070141,
      "full_scale_fraction": 0.0,
      "near_full_scale_fraction": 0.0,
      "measurement_method": "ffmpeg-loudnorm-oversampled-v1",
      "ffmpeg_version": "ffmpeg version 9.0.1 Copyright (c) 2000-2026 the FFmpeg developers",
      "source_sha256": "29a639a5230f2a3301e26d6b9984ee57a7928eeefa2e1998a45b47e15cd40c09",
      "samples_analyzed": 288000,
      "warnings": []
    },
    "target_result": "off",
    "engines": [
      {
        "name": "ffmpeg",
        "revision": "ffmpeg-cpu-dsp-v1",
        "configuration_sha256": "5d1cfc516b912249aabed7f2391b882a35a068107311def9aaf11e80f7985b96",
        "executable_sha256": "9f5ddd4f8b36eb07b0c5d60ab6ea1e618bedd70bc45feab82b4dc23be1596ad1",
        "recipe": "direct-v1",
        "input_gain_power_of_two": 0,
        "output_gain_power_of_two": 0,
        "license_evidence_ids": []
      }
    ],
    "configuration_sha256": "a9d46e021471d285a9316c6b65713eaa13e9f327e62da0319ddf522cea82d3ed",
    "warnings": [],
    "created_at": "2026-10-17T11:27:47.973766+00:00",
    "request": {
      "schema_version": 1,
      "source": {
        "kind": "production_asset",
        "asset_id": "4a6f6b05bbf24c25aadcc596570dfc30",
        "sha256": "1007b218b589d7b6a2bdb567428bce612744b5f76dd95ec1f56eaf4f9c33f9ca"
      },
      "source_sha256": "1007b218b589d7b6a2bdb567428bce612744b5f76dd95ec1f56eaf4f9c33f9ca",
      "revision": 0,
      "sample_rate_hz": null,
      "channels": null,
      "anchors": [
        {
          "source_sec": 0.0,
          "target_sec": 0.0
        },
        {
          "source_sec": 3.0,
          "target_sec": 3.0
        }
      ],
      "output_duration_sec": 3.0,
      "pitch_shift_semitones": 0.0,
      "min_duration_ratio": 0.75,
      "max_duration_ratio": 1.5,
      "gain_db": 6.0,
      "eq_bands": [],
      "compression": null,
      "fades": {
        "fade_in_sec": 0.0,
        "fade_out_sec": 0.0,
        "curve": "linear"
      },
      "loudness": {
        "profile": "off",
        "integrated_lufs": -16.0,
        "true_peak_dbtp": -2.0
      }
    },
    "input_metrics": {
      "duration_sec": 3.0,
      "integrated_lufs": -26.75,
      "loudness_range_lu": 0.0,
      "true_peak_dbtp": -26.02,
      "sample_peak_dbfs": -26.020599783849786,
      "full_scale_fraction": 0.0,
      "near_full_scale_fraction": 0.0,
      "measurement_method": "ffmpeg-loudnorm-oversampled-v1",
      "ffmpeg_version": "ffmpeg version 9.0.1 Copyright (c) 2000-2026 the FFmpeg developers",
      "source_sha256": "1007b218b589d7b6a2bdb567428bce612744b5f76dd95ec1f56eaf4f9c33f9ca",
      "samples_analyzed": 288000,
      "warnings": []
    },
    "target_start_frame": 0,
    "applied_gain_db": 6.0
  },
  "mix": {
    "schema_version": 1,
    "id": "96f615d0e4364cfca1997b4500fdfd46",
    "revision": 0,
    "artifact_name": "96f615d0e4364cfca1997b4500fdfd46.wav",
    "audio_sha256": "0e29716735e4d5388231262af6a301aeabf064182eb2c4e1dfc42bc85693cfaa",
    "sample_rate_hz": 48000,
    "channels": 2,
    "duration_frames": 144000,
    "output_metrics": {
      "duration_sec": 3.0,
      "integrated_lufs": -20.45,
      "loudness_range_lu": 0.0,
      "true_peak_dbtp": -18.08,
      "sample_peak_dbfs": -18.07827572588489,
      "full_scale_fraction": 0.0,
      "near_full_scale_fraction": 0.0,
      "measurement_method": "ffmpeg-loudnorm-oversampled-v1",
      "ffmpeg_version": "ffmpeg version 9.0.1 Copyright (c) 2000-2026 the FFmpeg developers",
      "source_sha256": "0e29716735e4d5388231262af6a301aeabf064182eb2c4e1dfc42bc85693cfaa",
      "samples_analyzed": 288000,
      "warnings": []
    },
    "target_result": "off",
    "engines": [
      {
        "name": "ffmpeg",
        "revision": "ffmpeg-cpu-dsp-v1",
        "configuration_sha256": "c527c8f226be4c1514e8813aa9352a8353a95c561dc060fa5d1b30613fa4ebb4",
        "executable_sha256": "9f5ddd4f8b36eb07b0c5d60ab6ea1e618bedd70bc45feab82b4dc23be1596ad1",
        "recipe": "direct-v1",
        "input_gain_power_of_two": 0,
        "output_gain_power_of_two": 0,
        "license_evidence_ids": []
      }
    ],
    "configuration_sha256": "40eac8e9d4960883fe1efa1bfeba78b701022011e860f6577d452441bd393c27",
    "warnings": [],
    "created_at": "2026-10-17T11:27:48.183330+00:00",
    "request": {
      "schema_version": 1,
      "revision": 0,
      "sample_rate_hz": 48000,
      "channels": 2,
      "duration_sec": 3.0,
      "inputs": [
        {
          "id": "99a347be6ede4319b2a4e805777079df",
          "source": {
            "kind": "production_asset",
            "asset_id": "5869f47485aa488b9a94357496d35af4",
            "sha256": "29a639a5230f2a3301e26d6b9984ee57a7928eeefa2e1998a45b47e15cd40c09"
          },
          "source_sha256": "29a639a5230f2a3301e26d6b9984ee57a7928eeefa2e1998a45b47e15cd40c09",
          "source_start_sec": 0.0,
          "start_sec": 0.0,
          "duration_sec": 3.0,
          "gain_db": 0.0,
          "pan": 0.0,
          "duck": null
        },
        {
          "id": "bd260a82a7b348fc8996524f476b7c2e",
          "source": {
            "kind": "production_asset",
            "asset_id": "d6cdc39f709b4bba9df3d849b385b04d",
            "sha256": "6af013026e44a12ba3c1760ef4a4e4369034f85e166a9fdbd13598a1e5f55597"
          },
          "source_sha256": "6af013026e44a12ba3c1760ef4a4e4369034f85e166a9fdbd13598a1e5f55597",
          "source_start_sec": 0.0,
          "start_sec": 0.0,
          "duration_sec": 3.0,
          "gain_db": 0.0,
          "pan": 0.0,
          "duck": null
        }
      ],
      "loudness": {
        "profile": "off",
        "integrated_lufs": -16.0,
        "true_peak_dbtp": -2.0
      }
    },
    "input_metrics": [
      {
        "id": "99a347be6ede4319b2a4e805777079df",
        "source": {
          "kind": "production_asset",
          "asset_id": "5869f47485aa488b9a94357496d35af4",
          "sha256": "29a639a5230f2a3301e26d6b9984ee57a7928eeefa2e1998a45b47e15cd40c09"
        },
        "source_sha256": "29a639a5230f2a3301e26d6b9984ee57a7928eeefa2e1998a45b47e15cd40c09",
        "metrics": {
          "duration_sec": 3.0,
          "integrated_lufs": -20.75,
          "loudness_range_lu": 0.0,
          "true_peak_dbtp": -20.02,
          "sample_peak_dbfs": -20.02059974070141,
          "full_scale_fraction": 0.0,
          "near_full_scale_fraction": 0.0,
          "measurement_method": "ffmpeg-loudnorm-oversampled-v1",
          "ffmpeg_version": "ffmpeg version 9.0.1 Copyright (c) 2000-2026 the FFmpeg developers",
          "source_sha256": "29a639a5230f2a3301e26d6b9984ee57a7928eeefa2e1998a45b47e15cd40c09",
          "samples_analyzed": 288000,
          "warnings": []
        }
      },
      {
        "id": "bd260a82a7b348fc8996524f476b7c2e",
        "source": {
          "kind": "production_asset",
          "asset_id": "d6cdc39f709b4bba9df3d849b385b04d",
          "sha256": "6af013026e44a12ba3c1760ef4a4e4369034f85e166a9fdbd13598a1e5f55597"
        },
        "source_sha256": "6af013026e44a12ba3c1760ef4a4e4369034f85e166a9fdbd13598a1e5f55597",
        "metrics": {
          "duration_sec": 3.0,
          "integrated_lufs": -32.75,
          "loudness_range_lu": 0.0,
          "true_peak_dbtp": -32.04,
          "sample_peak_dbfs": -32.04119969712941,
          "full_scale_fraction": 0.0,
          "near_full_scale_fraction": 0.0,
          "measurement_method": "ffmpeg-loudnorm-oversampled-v1",
          "ffmpeg_version": "ffmpeg version 9.0.1 Copyright (c) 2000-2026 the FFmpeg developers",
          "source_sha256": "6af013026e44a12ba3c1760ef4a4e4369034f85e166a9fdbd13598a1e5f55597",
          "samples_analyzed": 288000,
          "warnings": []
        }
      }
    ]
  },
  "artifacts": [
    {
      "id": "4d802435a21d490d93bfea0170750764",
      "role": "original_vocal",
      "sha256": "f68cf8b65a97c9d3c3246a16543f525b662074e1c8aed7c4ad25d1eebc8cec38",
      "evidence_asset_id": "382cd694f31543a0a3a31f1f1bd7b957",
      "sample_rate_hz": 48000,
      "channels": 2,
      "duration_frames": 144000,
      "audio_url": "/api/tracks/1/versions/20a994cfe1aa4cf2ab654c38fec2de87/mix/artifacts/4d802435a21d490d93bfea0170750764"
    },
    {
      "id": "8ccc8620a6b84c9b9de19e55a1986874",
      "role": "converted_dry",
      "sha256": "1007b218b589d7b6a2bdb567428bce612744b5f76dd95ec1f56eaf4f9c33f9ca",
      "evidence_asset_id": "4a6f6b05bbf24c25aadcc596570dfc30",
      "sample_rate_hz": 48000,
      "channels": 2,
      "duration_frames": 144000,
      "audio_url": "/api/tracks/1/versions/20a994cfe1aa4cf2ab654c38fec2de87/mix/artifacts/8ccc8620a6b84c9b9de19e55a1986874"
    },
    {
      "id": "b257977b228d4950b1e89579326ce716",
      "role": "backing",
      "sha256": "6af013026e44a12ba3c1760ef4a4e4369034f85e166a9fdbd13598a1e5f55597",
      "evidence_asset_id": "d6cdc39f709b4bba9df3d849b385b04d",
      "sample_rate_hz": 48000,
      "channels": 2,
      "duration_frames": 144000,
      "audio_url": "/api/tracks/1/versions/20a994cfe1aa4cf2ab654c38fec2de87/mix/artifacts/b257977b228d4950b1e89579326ce716"
    },
    {
      "id": "43b7c694f33c4a05a3893709d2c9e049",
      "role": "matched_vocal",
      "sha256": "29a639a5230f2a3301e26d6b9984ee57a7928eeefa2e1998a45b47e15cd40c09",
      "evidence_asset_id": "5869f47485aa488b9a94357496d35af4",
      "sample_rate_hz": 48000,
      "channels": 2,
      "duration_frames": 144000,
      "audio_url": "/api/tracks/1/versions/20a994cfe1aa4cf2ab654c38fec2de87/mix/artifacts/43b7c694f33c4a05a3893709d2c9e049"
    },
    {
      "id": "96f615d0e4364cfca1997b4500fdfd46",
      "role": "master",
      "sha256": "0e29716735e4d5388231262af6a301aeabf064182eb2c4e1dfc42bc85693cfaa",
      "evidence_asset_id": "4919942eee22424691b8d8adba14a6d8",
      "sample_rate_hz": 48000,
      "channels": 2,
      "duration_frames": 144000,
      "audio_url": "/api/tracks/1/versions/20a994cfe1aa4cf2ab654c38fec2de87/mix/artifacts/96f615d0e4364cfca1997b4500fdfd46"
    }
  ],
  "created_at": "2026-10-17T11:27:48.187018+00:00"
}
