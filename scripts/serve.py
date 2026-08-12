"""Launch the API with proxy trust configured coherently.

Use this instead of a bare `uvicorn app.main:app`.

WHY THIS EXISTS
---------------
Uvicorn enables ProxyHeadersMiddleware by default with
forwarded_allow_ips="127.0.0.1". That middleware rewrites request.client.host
from the X-Forwarded-For header *before* any application code runs. So a
client connecting from localhost — or from anywhere, once you deploy behind a
proxy that forwards to loopback — could forge X-Forwarded-For and get a fresh
rate-limit bucket per request, no matter what the application checked.

Verified: with uvicorn's defaults, 3 requests with different forged
X-Forwarded-For values all returned 200 after the limit was exhausted. With
proxy header trust bound to TRUSTED_PROXIES (below), they correctly return 429.

This entrypoint binds uvicorn's proxy trust to the same TRUSTED_PROXIES setting
the application uses, so there is exactly one place to configure it:

  TRUSTED_PROXIES empty  -> proxy headers ignored entirely (direct exposure)
  TRUSTED_PROXIES set    -> headers honoured only from those addresses
"""
import argparse
import logging
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import uvicorn
from app.config import settings

logger = logging.getLogger("serve")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the LegalX Shorts API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="Development auto-reload")
    args = parser.parse_args()

    trust_proxies = bool(settings.trusted_proxies)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if trust_proxies:
        logger.info(
            "Trusting proxy headers from: %s", ", ".join(settings.trusted_proxies)
        )
    else:
        logger.info(
            "Proxy headers disabled (TRUSTED_PROXIES is empty). Rate limits will "
            "key on the direct peer address."
        )

    if settings.environment == "production" and args.host == "127.0.0.1":
        logger.warning("Binding to 127.0.0.1 in production — set --host 0.0.0.0 if this is a container.")

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        # The critical pair: never trust forwarded headers unless we know which
        # hop is allowed to set them.
        proxy_headers=trust_proxies,
        forwarded_allow_ips=",".join(settings.trusted_proxies) if trust_proxies else None,
        server_header=False,   # don't advertise the server version
        date_header=True,
    )


if __name__ == "__main__":
    main()
