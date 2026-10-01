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


def blocked_when_off(path: str) -> bool:
    """True if this path must answer 'Service offline' while the switch is off."""
    p = path.rstrip("/") or "/"
    if p != API_PREFIX and not p.startswith(API_PREFIX + "/"):
        return False
    return p not in ALWAYS_ALLOWED
