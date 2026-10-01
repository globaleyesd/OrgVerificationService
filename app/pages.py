"""The web pages and their addresses, plus the content-security policy.

Each page is its own address. CloudFront (AWS) and the local server both map these
addresses to the files; tests check the two stay in sync.
"""

import hashlib
import re
from pathlib import Path

PAGES = {
    "/": "index.html",      # small launcher with two links
    "/ask": "ask.html",     # role-checked
    "/add": "add.html",     # no role check
    "/review": "review.html",   # top role only: mark which documents each level can read
    "/signin": "signin.html",   # sign in, or switch between the demo accounts
}

# No inline scripts or styles are allowed: every script and style is a separate file served from this site.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


_ASSET = re.compile(r'(src|href)="([A-Za-z0-9_-]+\.(?:js|css))"')


def with_versions(html: str, web_dir: Path) -> str:
    """Point each script and stylesheet at an address that includes a fingerprint of its contents
    ("ask.js?v=1a2b3c4d"). When a file changes its address changes, so a browser can never keep running an
    old copy after an update; an unchanged file keeps its address and is not downloaded again."""
    def stamp(m):
        f = web_dir / m.group(2)
        if not f.is_file():
            return m.group(0)
        return f'{m.group(1)}="{m.group(2)}?v={hashlib.sha256(f.read_bytes()).hexdigest()[:10]}"'
    return _ASSET.sub(stamp, html)
