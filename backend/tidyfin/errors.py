"""
Application errors.

Each error has a unique code and an English message.
The dashboard translates errors by code, keeping API messages in English for easier bug reporting while displaying them in the selected language.
"""
from __future__ import annotations


class AppError(Exception):
    status = 400
    code = "error"

    def __init__(self, message: str = "", code: str | None = None, status: int | None = None):
        super().__init__(message or self.__class__.__name__)
        if code:
            self.code = code
        if status:
            self.status = status


class BadRequest(AppError):
    status, code = 400, "bad_request"


class AuthError(AppError):
    status, code = 401, "invalid_credentials"


class Forbidden(AppError):
    status, code = 403, "forbidden"


class NotFound(AppError):
    status, code = 404, "not_found"


class Conflict(AppError):
    status, code = 409, "conflict"


class RateLimited(AppError):
    status, code = 429, "rate_limited"


class SetupError(AppError):
    status, code = 400, "setup_error"


class UpstreamError(AppError):
    """An external service (Jellyfin/Radarr/Sonarr) did not answer correctly."""
    status, code = 502, "upstream_error"
