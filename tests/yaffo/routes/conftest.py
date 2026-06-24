"""Shared fixtures for Flask route tests.

Each test gets the real application wired up by `create_app()` but pointed at a
fresh, throwaway SQLite database (create_app otherwise targets the production
app.db), so route tests are isolated and order-independent. Generation is gated on
an Anthropic API key that may live in the dev machine's keychain, so by default we
neutralise it — the chat route is deterministic unless a test opts in.
"""
import pytest

from yaffo import themes
from yaffo.app import create_app
from yaffo.db import db


@pytest.fixture
def app(tmp_path):
    application = create_app(db_path=tmp_path / "test.db", config={"TESTING": True})
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def reset_theme_cache():
    """themes caches the active slug in a module global that outlives each
    test's throwaway database; reset it so tests stay order-independent."""
    themes._cached_theme = None
    yield
    themes._cached_theme = None


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch):
    """Default every route test to 'no API key' so the chat route never reaches a
    real model. Tests that exercise generation override get_api_key + create_agent."""
    monkeypatch.setattr("yaffo.site_agents.llm_config.get_api_key", lambda *a, **k: None)