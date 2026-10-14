"""Deterministic synthetic PCM fixtures; these are not singer-quality evidence."""
from __future__ import annotations

import io
import math
import struct
import wave


def tone_wav_bytes(*, seconds: float = 1.0, frequency_hz: float = 220.0,
                   amplitude: float = .2, rate: int = 44100, channels: int = 1) -> bytes:
    if not math.isfinite(seconds) or not 0 < seconds <= 30:
        raise ValueError('seconds must be finite and within (0, 30]')
    if not 8000 <= rate <= 192000 or channels not in (1, 2):
        raise ValueError('unsupported PCM rate or channel count')
    if not math.isfinite(frequency_hz) or not 0 < frequency_hz < rate / 2:
        raise ValueError('frequency must be below the Nyquist limit')
    if not math.isfinite(amplitude) or not 0 <= amplitude <= 1:
        raise ValueError('amplitude must be within [0, 1]')
    frames = round(seconds * rate)
    if frames == 0:
        raise ValueError('duration must contain at least one frame')
    samples = b''.join(struct.pack('<h', round(amplitude * 32767 * math.sin(2 * math.pi * frequency_hz * frame / rate)))
                       * channels for frame in range(frames))
    output = io.BytesIO()
    with wave.open(output, 'wb') as recording:
        recording.setnchannels(channels)
        recording.setsampwidth(2)
        recording.setframerate(rate)
        recording.writeframes(samples)
    return output.getvalue()


def _self_check() -> None:
    audio = tone_wav_bytes(seconds=.1, channels=2)
    assert audio == tone_wav_bytes(seconds=.1, channels=2)
    assert audio != tone_wav_bytes(seconds=.1, frequency_hz=330, channels=2)
    with wave.open(io.BytesIO(audio), 'rb') as recording:
        assert (recording.getframerate(), recording.getnchannels(), recording.getsampwidth(), recording.getnframes()) == (44100, 2, 2, 4410)
        samples = struct.unpack('<' + 'h' * 8820, recording.readframes(4410))
    assert samples[::2] == samples[1::2]
    assert min(samples) < 0 < max(samples) <= 6554
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples)) / 32767
    assert abs(rms - .2 / math.sqrt(2)) < .0001
    for seconds in (0, float('nan'), 31):
        try:
            tone_wav_bytes(seconds=seconds)
        except ValueError:
            pass
        else:
            raise AssertionError('invalid duration was accepted')


if __name__ == '__main__':
    _self_check()
