"""Yaffo's own OAuth app registrations for the preset providers.

These are desktop (public) clients: sign-in uses PKCE and a loopback return
address, and nothing here is confidential. Anyone can read them out of a shipped
app, as with every open-source desktop app. Google issues a "secret" even for
desktop clients; its token endpoint expects it, but it protects nothing. See
docs/development/oauth-app-registrations.md.

The Google secret is still kept out of the repository, because secret scanners
reject it on push. Builds stamp it into the generated `yaffo/_build_info.py`
from `YAFFO_OAUTH_GOOGLE_CLIENT_SECRET` (see packaging/bump_version.py); a dev
checkout reads the same variable at run time.

Environment variables override the committed values for development builds.
"""
from __future__ import annotations

import os

try:
    from yaffo import _build_info  # generated at build time; absent in a dev checkout
except ImportError:
    _build_info = None

MICROSOFT_CLIENT_ID = "de78c8bb-c682-4f8c-853c-979edc53a42c"
GOOGLE_CLIENT_ID = "150163256384-1mduv8hmpulmc4tmu4nhquu19rcsp5ff.apps.googleusercontent.com"


def microsoft_client_id() -> str:
    return os.environ.get("YAFFO_OAUTH_MICROSOFT_CLIENT_ID") or MICROSOFT_CLIENT_ID


def google_client_id() -> str:
    return os.environ.get("YAFFO_OAUTH_GOOGLE_CLIENT_ID") or GOOGLE_CLIENT_ID


def google_client_secret() -> str | None:
    """Desktop client secrets identify the app but are not confidential."""
    return (os.environ.get("YAFFO_OAUTH_GOOGLE_CLIENT_SECRET")
            or getattr(_build_info, "OAUTH_GOOGLE_CLIENT_SECRET", None))
