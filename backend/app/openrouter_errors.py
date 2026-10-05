"""Stable local codes only; upstream exception text is deliberately excluded."""
from __future__ import annotations
class OpenRouterError(Exception):
    def __init__(self, code: str, status: int = 503) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
