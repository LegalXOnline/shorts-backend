import ipaddress
import logging
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler
from app.config import settings
from app.rate_limit import limiter

logger = logging.getLogger(__name__)


def _is_trusted_peer(peer: str | None) -> bool:
    """Is the immediate connection peer a proxy we configured?

    settings.trusted_proxies accepts plain addresses and CIDR blocks.
    """
    if not peer or not settings.trusted_proxies:
        return False
    try:
        peer_addr = ipaddress.ip_address(peer)
    except ValueError:
        return False

    for entry in settings.trusted_proxies:
        try:
            if peer_addr in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            logger.warning("Ignoring invalid trusted_proxies entry: %r", entry)
    return False


def get_client_ip(request: Request) -> str:
    """Rate-limit key: the client IP, trusting X-Forwarded-For only behind a proxy.

    Previously X-Forwarded-For was honoured unconditionally, so any client
    could rotate the header per request and get unlimited quota. The header is
    now only read when the immediate peer is a configured trusted proxy.
    """
    peer = request.client.host if request.client else None

    if _is_trusted_peer(peer):
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            # Rightmost entry that is not itself a trusted proxy is the closest
            # address the trusted hop actually observed; leftmost entries are
            # client-supplied and forgeable.
            hops = [h.strip() for h in forwarded.split(",") if h.strip()]
            for hop in reversed(hops):
                if not _is_trusted_peer(hop):
                    return hop
            if hops:
                return hops[0]

    return get_remote_address(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Baseline hardening headers, including a CSP for the reviewer HTML page."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "script-src 'self' 'unsafe-inline'; "
            "frame-ancestors 'none'; "
            "base-uri 'none'; "
            "form-action 'none'",
        )
        if settings.environment == "production":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response


# C-2: Disable /docs and /openapi.json in production to prevent API reconnaissance
app = FastAPI(
    title="LegalX Shorts API",
    docs_url="/docs" if settings.environment == "development" else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.environment == "development" else None,
)
app.state.limiter = limiter

# Add exception handler for rate limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Setup middleware
if settings.environment == "production":
    app.add_middleware(HTTPSRedirectMiddleware)
    if not settings.trusted_proxies:
        logger.warning(
            "trusted_proxies is empty — X-Forwarded-For will be ignored and rate "
            "limits will key on the proxy's own IP. Set TRUSTED_PROXIES to your "
            "load balancer / CDN address range."
        )

app.add_middleware(SecurityHeadersMiddleware)

# S-6: CORS origins driven by config — set ALLOWED_ORIGINS in .env for production
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

from app.api import feed, preview

app.include_router(feed.router)

# The preview router can publish and delete cards, so it is only mounted when
# explicitly enabled — and in production only when reviewer credentials exist.
# It used to be mounted unconditionally with no authentication at all.
if settings.enable_preview_ui:
    if settings.preview_requires_auth and not settings.preview_credentials_set:
        logger.error(
            "Refusing to mount the reviewer preview UI: ENABLE_PREVIEW_UI is on in "
            "production but REVIEWER_USERNAME / REVIEWER_PASSWORD (min 12 chars) "
            "are not set. The preview endpoints can publish and delete content."
        )
    else:
        app.include_router(preview.router)
        if not settings.preview_credentials_set:
            logger.warning(
                "Reviewer preview UI mounted WITHOUT authentication (development "
                "only). Set REVIEWER_USERNAME / REVIEWER_PASSWORD to protect it."
            )


@app.get("/health")
def health_check():
    return {"status": "ok", "environment": settings.environment}
