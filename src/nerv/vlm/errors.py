"""Stable public errors. Never serialize SDK exceptions, headers or request bodies."""
from __future__ import annotations

MESSAGES = {
    "key_missing": "Configure an API key in model settings first.",
    "key_invalid": "The API key is invalid or expired. Configure it again.",
    "quota_exceeded": "The provider account has insufficient credit or quota.",
    "rate_limited": "Too many requests. Try again later.",
    "model_unavailable": "This model is unavailable. Check the model ID and account access.",
    "request_unsupported": "The model API does not support this request. Check model settings.",
    "network_error": "Cannot connect to the model service. Try again.",
    "request_timeout": "The model request timed out. Try again.",
    "provider_unavailable": "The model service is temporarily unavailable.",
    "invalid_response": "The model returned an invalid response. This operation was not executed.",
    "vision_mismatch": "The image answer did not match. The key was not saved.",
    "configuration_changed": "Settings changed during the test. Submit again.",
    "configuration_cancelled": "Configuration was cancelled. The key was not saved.",
    "storage_failed": "The key could not be saved. Check private storage permissions.",
}


class ModelError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(MESSAGES[code])

    def public(self):
        return {"code": self.code, "message": str(self)}


def model_error(error: Exception) -> ModelError:
    if isinstance(error, ModelError):
        return error
    status = getattr(error, "status_code", None) or getattr(error, "code", None)
    # Only inspect provider error codes; never expose arbitrary exception text.
    body = getattr(error, "body", None)
    detail = body.get("error", body) if isinstance(body, dict) else {}
    provider_code = str(detail.get("code", detail.get("type", ""))) if isinstance(detail, dict) else ""
    name = type(error).__name__.lower()
    if status == 401 or "authentication" in name:
        code = "key_invalid"
    elif status == 402 or provider_code in {"insufficient_quota", "insufficient_balance", "credit_balance_too_low", "RESOURCE_EXHAUSTED_QUOTA"}:
        code = "quota_exceeded"
    elif status == 429 or "ratelimit" in name:
        code = "rate_limited"
    elif status in (403, 404):
        code = "model_unavailable"
    elif status in (400, 422):
        code = "request_unsupported"
    elif status == 408 or "timeout" in name:
        code = "request_timeout"
    elif isinstance(status, int) and status >= 500:
        code = "provider_unavailable"
    elif isinstance(error, ConnectionError) or "connect" in name:
        code = "network_error"
    else:
        code = "invalid_response"
    return ModelError(code)
