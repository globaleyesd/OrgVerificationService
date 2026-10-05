"""Which requests may pass while the service is OFF. Pure, so it is easy to test.

Everything under /api is blocked except the paths below. Non-API paths (the web
page itself) are static files and are not gated; the page shows the offline screen.
"""
from __future__ import annotations

API_PREFIX = "/api"

# Always reachable: liveness for the container health check, status for the page,
# and the password-protected switch itself.
ALWAYS_ALLOWED = frozenset({
    "/api/health",
    "/api/service/status",
    "/api/service/on",
    "/api/service/off",
})

OFFLINE_MESSAGE = "Service offline"


def from_our_cdn(path: str, header: str | None, secret: str) -> bool:
    """On AWS only CloudFront may reach the server, and it proves it with a secret header. Anything else (for example
    someone else's CloudFront pointed at the server's address) is refused. No secret configured = local run, no check.
    The health check stays open: it says nothing and the container check calls it directly."""
    import hmac
    if not secret or path == "/api/health":
        return True
    return hmac.compare_digest((header or "").encode(), secret.encode())


def blocked_when_off(path: str) -> bool:
    """True if this path must answer 'Service offline' while the switch is off."""
    p = path.rstrip("/") or "/"
    if p != API_PREFIX and not p.startswith(API_PREFIX + "/"):
        return False
    return p not in ALWAYS_ALLOWED
