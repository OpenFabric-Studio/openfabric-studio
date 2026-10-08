"""Small real reference recordings shared by backend profile tests."""
from __future__ import annotations

import io
import wave


def wav_bytes(*, frames: int = 1600, rate: int = 16000, channels: int = 1, width: int = 2) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as recording:
        recording.setnchannels(channels)
        recording.setsampwidth(width)
        recording.setframerate(rate)
        recording.writeframes(b"\0" * (frames * channels * width))
    return output.getvalue()
