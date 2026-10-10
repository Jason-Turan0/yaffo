"""Yaffo's own OAuth app registrations for the preset providers.

These are desktop (public) clients: sign-in uses PKCE and a loopback return
address, and nothing here is confidential. Anyone can read them out of a shipped
app, as with every open-source desktop app. Google issues a "secret" even for
desktop clients; its token endpoint expects it, but it protects nothing. See
docs/development/oauth-app-registrations.md.

Environment variables override the committed values for development builds.
"""
from __future__ import annotations

import os

MICROSOFT_CLIENT_ID = "de78c8bb-c682-4f8c-853c-979edc53a42c"
GOOGLE_CLIENT_ID = "150163256384-1mduv8hmpulmc4tmu4nhquu19rcsp5ff.apps.googleusercontent.com"


def microsoft_client_id() -> str:
    return os.environ.get("YAFFO_OAUTH_MICROSOFT_CLIENT_ID") or MICROSOFT_CLIENT_ID


def google_client_id() -> str:
    return os.environ.get("YAFFO_OAUTH_GOOGLE_CLIENT_ID") or GOOGLE_CLIENT_ID


def google_client_secret() -> str | None:
    """Phase 3 (the Google Photos target) commits it; until then it comes from the
    maintainer's environment."""
    return os.environ.get("YAFFO_OAUTH_GOOGLE_CLIENT_SECRET")
