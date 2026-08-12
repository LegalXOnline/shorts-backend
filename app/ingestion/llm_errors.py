"""Groq error classification for the retry loops.

Every LLM call site previously used `except (ValidationError, Exception)`, which
made an invalid API key, an exhausted quota and a malformed JSON response all
look identical in the logs ("attempt 1/3 failed") and burned three attempts on
errors that could never succeed.
"""
from groq import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)

# Retrying will never help: fix the config or the request.
FATAL_LLM_ERRORS = (
    AuthenticationError,
    PermissionDeniedError,
    NotFoundError,     # e.g. the configured model name does not exist
    BadRequestError,   # malformed request / context length exceeded
)

# Transient: worth another attempt with backoff.
RETRYABLE_LLM_ERRORS = (
    RateLimitError,
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
)

__all__ = ["FATAL_LLM_ERRORS", "RETRYABLE_LLM_ERRORS"]
