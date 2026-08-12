"""Shared rate limiter.

Lives in its own module so routers can apply @limiter.limit(...) decorators
without importing app.main (which imports the routers — a circular import).

The key function is injected by app.main once middleware config is known.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address
from app.config import settings


def _key_func(request):
    # Late import: app.main defines the trusted-proxy aware resolver.
    from app.main import get_client_ip
    return get_client_ip(request)


limiter = Limiter(key_func=_key_func, default_limits=[])

FEED_LIMIT = settings.feed_rate_limit
PREVIEW_LIMIT = settings.preview_rate_limit

__all__ = ["limiter", "FEED_LIMIT", "PREVIEW_LIMIT", "get_remote_address"]
