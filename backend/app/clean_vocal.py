"""Conservative tail attenuation for auditioning, not guaranteed dereverberation.

Stable sustained content stays dry. Only quiet falling spectral tails receive
bounded attenuation, with no makeup gain or normalization.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal
from numpy.typing import NDArray

AudioArray = NDArray[np.float32] | NDArray[np.float64]


def clean_vocal(audio: AudioArray, sample_rate: int) -> AudioArray:
    """audio is float samples shaped (samples, channels)."""
    audio = np.asarray(audio, dtype=np.float64)
    if audio.ndim == 1:
        audio = audio[:, None]
    if not np.isfinite(audio).all():
        raise ValueError('nonfinite_audio')
    if audio.ndim != 2 or not 1 <= audio.shape[1] <= 32:
        raise ValueError('invalid_audio_shape')
    if audio.size == 0 or sample_rate <= 0:
        return audio
    cleaned = np.stack(
        [_clean_channel(audio[:, index], sample_rate) for index in range(audio.shape[1])],
        axis=1,
    )
    return cleaned.astype(np.float32)


def _clean_channel(channel: AudioArray, sample_rate: int) -> AudioArray:
    if channel.size < 2048:
        return channel.copy()
    frame = 2048
    hop = 512
    window = signal.windows.hann(frame, sym=False)
    overlap = frame - hop
    _times, _freqs, spectrum = signal.stft(
        channel,
        fs=sample_rate,
        window=window,
        nperseg=frame,
        noverlap=overlap,
        boundary="zeros",
        padded=True,
    )
    magnitude = np.abs(spectrum)
    power = magnitude ** 2
    delay = max(1, int(0.04 * sample_rate / hop))
    decay = 0.85
    # Attenuate only low-level decaying tails; stable sustains remain dry.
    strength = 0.15
    floor = 0.85
    late = np.zeros_like(power)
    carry = np.zeros(power.shape[0], dtype=np.float64)
    for time_index in range(power.shape[1]):
        if time_index >= delay:
            carry = decay * carry + (1.0 - decay) * power[:, time_index - delay]
            late[:, time_index] = carry
        else:
            carry = decay * carry
    previous_power = np.pad(power[:, :-1], ((0, 0), (1, 0)))
    falling = (power < late * 0.5) & (power < previous_power * 0.95) & (power < np.max(power, axis=1, keepdims=True) * 0.01)
    gain = 1.0 - strength * np.where(falling, late / (power + 1e-8), 0.0)
    gain = np.clip(gain, floor, 1.0)
    _time, restored = signal.istft(
        magnitude * gain * np.exp(1j * np.angle(spectrum)),
        fs=sample_rate,
        window=window,
        nperseg=frame,
        noverlap=overlap,
        input_onesided=True,
        boundary=True,
    )
    if restored.shape[0] < channel.shape[0]:
        restored = np.pad(restored, (0, channel.shape[0] - restored.shape[0]))
    restored = restored[: channel.shape[0]]
    # No RMS makeup gain: it would amplify surviving noise and flatten dynamics.
    peak = float(np.max(np.abs(channel)))
    return np.asarray(np.clip(restored, -peak, peak), dtype=np.float64)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: clean_vocal.py INPUT OUTPUT", file=sys.stderr)
        return 2
    source = Path(argv[1])
    dest = Path(argv[2])
    audio, sample_rate = sf.read(source, always_2d=True)
    cleaned = clean_vocal(audio, int(sample_rate))
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix('.partial.wav')
    try:
        sf.write(partial, cleaned, int(sample_rate), subtype="FLOAT")
        partial.replace(dest)
    finally:
        partial.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
