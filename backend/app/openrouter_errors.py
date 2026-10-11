"""Stable local codes only; upstream exception text is deliberately excluded."""
from __future__ import annotations

def rejection_code(status: int | None) -> str | None:
    """Only these received statuses prove that a paid request was rejected."""
    return {400:'provider_request_rejected',404:'provider_request_rejected',405:'provider_request_rejected',
        413:'provider_request_rejected',415:'provider_request_rejected',422:'provider_request_rejected',
        401:'credential_rejected',402:'provider_credit_limit',403:'provider_access_denied',429:'provider_rate_limited'}.get(status) if status is not None else None

class OpenRouterError(Exception):
    def __init__(self, code: str, status: int = 503, *, provider_http_status: int | None = None) -> None:
        if provider_http_status is not None and (type(provider_http_status) is not int or not 100 <= provider_http_status <= 599):
            raise ValueError('Invalid provider HTTP status')
        super().__init__(code)
        self.code = code
        self.status = status
        self.provider_http_status = provider_http_status
