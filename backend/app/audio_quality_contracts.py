"""Explicit house targets and measured audio diagnostics; no compliance claim."""
from __future__ import annotations
from typing import Literal
from pydantic import ConfigDict, Field
from .contracts import Contract


class LoudnessSettings(Contract):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    profile: Literal['off','music','spoken_word','ebu','custom']='off'
    integrated_lufs: float=Field(default=-16,ge=-40,le=-8)
    true_peak_dbtp: float=Field(default=-2,ge=-9,le=-.1)

    @property
    def target(self) -> tuple[float,float] | None:
        if self.profile=='off':return None
        if self.profile=='music':return -14,-1
        if self.profile=='spoken_word':return -16,-2
        if self.profile=='ebu':return -23,-1
        return self.integrated_lufs,self.true_peak_dbtp


class AudioMetrics(Contract):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    duration_sec: float=Field(ge=0,le=86400)
    integrated_lufs: float | None=Field(default=None,ge=-200,le=100)
    loudness_range_lu: float | None=Field(default=None,ge=0,le=200)
    true_peak_dbtp: float | None=Field(default=None,ge=-200,le=100)
    sample_peak_dbfs: float | None=Field(default=None,ge=-200,le=100)
    full_scale_fraction: float=Field(default=0,ge=0,le=1)
    near_full_scale_fraction: float=Field(default=0,ge=0,le=1)
    measurement_method: Literal['ffmpeg-loudnorm-oversampled-v1']='ffmpeg-loudnorm-oversampled-v1'
    ffmpeg_version: str=Field(default='unavailable',max_length=200)
    source_sha256: str=Field(default='',pattern=r'^(?:[0-9a-f]{64})?$')
    samples_analyzed: int=Field(default=0,ge=0)
    warnings: list[Literal['silence','full_scale_samples','true_peak_over','short_programme']]=Field(default_factory=list,max_length=4)


TargetResult=Literal['off','met','warning','inconclusive']
