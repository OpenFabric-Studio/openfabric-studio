"""Narrow app-used declarations from python-soundfile 0.14.0 source.

The installed module has annotations but no PEP 561 marker. These declarations
cover path-based read/write calls and all four supported NumPy dtypes. read()
defaults to float64; out buffers and virtual IO are deliberately outside this
small declared surface. No runtime implementation is replaced.
Source: https://github.com/bastibe/python-soundfile (soundfile.py, read/write/info).
"""
from os import PathLike
from typing import Literal, overload

import numpy as np
from numpy.typing import NDArray

FilePath = str | PathLike[str]
AudioData = NDArray[np.float64] | NDArray[np.float32] | NDArray[np.int32] | NDArray[np.int16]

class _SoundFileInfo:
    samplerate: int
    channels: int
    frames: int
    duration: float
    format: str
    subtype: str

def info(file: FilePath, verbose: bool = False) -> _SoundFileInfo: ...

@overload
def read(file: FilePath, frames: int = -1, start: int = 0,
         stop: int | None = None, dtype: Literal['float64'] = 'float64',
         always_2d: bool = False, fill_value: float | None = None, out: None = None,
         samplerate: int | None = None, channels: int | None = None,
         format: str | None = None, subtype: str | None = None,
         endian: str | None = None, closefd: bool = True) -> tuple[NDArray[np.float64], int]: ...

@overload
def read(file: FilePath, frames: int = -1, start: int = 0,
         stop: int | None = None, dtype: Literal['float32'] = ...,
         always_2d: bool = False, fill_value: float | None = None, out: None = None,
         samplerate: int | None = None, channels: int | None = None,
         format: str | None = None, subtype: str | None = None,
         endian: str | None = None, closefd: bool = True) -> tuple[NDArray[np.float32], int]: ...

@overload
def read(file: FilePath, frames: int = -1, start: int = 0,
         stop: int | None = None, dtype: Literal['int32'] = ...,
         always_2d: bool = False, fill_value: float | None = None, out: None = None,
         samplerate: int | None = None, channels: int | None = None,
         format: str | None = None, subtype: str | None = None,
         endian: str | None = None, closefd: bool = True) -> tuple[NDArray[np.int32], int]: ...

@overload
def read(file: FilePath, frames: int = -1, start: int = 0,
         stop: int | None = None, dtype: Literal['int16'] = ...,
         always_2d: bool = False, fill_value: float | None = None, out: None = None,
         samplerate: int | None = None, channels: int | None = None,
         format: str | None = None, subtype: str | None = None,
         endian: str | None = None, closefd: bool = True) -> tuple[NDArray[np.int16], int]: ...

def write(file: FilePath, data: AudioData, samplerate: int,
          subtype: str | None = None, endian: str | None = None,
          format: str | None = None, closefd: bool = True,
          compression_level: float | None = None,
          bitrate_mode: str | None = None) -> None: ...
