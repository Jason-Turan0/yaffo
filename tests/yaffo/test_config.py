"""get_int validation + the config migration (_render): the template is the source of
truth for structure/comments/defaults, and a user's set values are merged into it on
startup so existing files gain new sections without losing customizations."""
import tomllib

import pytest

import yaffo.config as config
from yaffo.config import get_int, _render, _TEMPLATE

pytestmark = pytest.mark.unit


def test_template_is_valid_toml():
    # Guards against a duplicate-table / syntax slip that would zero out all config.
    tomllib.loads(_TEMPLATE)


class TestRenderMigration:
    def test_preserves_user_values_and_adds_new_keys(self):
        user = {"logging": {"level": "DEBUG"}, "database": {"synchronous": "FULL"}}
        merged = tomllib.loads(_render(user))
        # user overrides preserved
        assert merged["logging"]["level"] == "DEBUG"
        assert merged["database"]["synchronous"] == "FULL"
        # new keys/sections filled from the template defaults
        assert merged["logging"]["max_model_log_runs"] == 50
        assert merged["web"]["port"] == 5001
        assert merged["ai"]["max_iterations"] == 25

    def test_preserves_user_web_port(self):
        merged = tomllib.loads(_render({"web": {"port": 8123}}))
        assert merged["web"]["port"] == 8123

    def test_activates_commented_default_when_user_sets_it(self):
        # `# workers = 4` is commented by default; setting it should make it active.
        assert "\nworkers = 2" in _render({"tasks": {"workers": 2}})

    def test_leaves_commented_default_commented_when_unset(self):
        assert "# workers = 4" in _render({})

    def test_output_is_valid_toml_and_preserves_comments(self):
        rendered = _render({"logging": {"level": "WARNING"}})
        tomllib.loads(rendered)  # parses
        assert "# Verbosity of the log files" in rendered  # doc comments survive

    def test_unknown_user_keys_are_dropped(self):
        merged = tomllib.loads(_render({"logging": {"bogus_removed_key": 1}}))
        assert "bogus_removed_key" not in merged.get("logging", {})


@pytest.fixture
def cfg(monkeypatch):
    """Inject an in-memory config so tests don't depend on a config.toml on disk."""
    def _set(data):
        monkeypatch.setattr(config, "_config", data)
    return _set


def test_reads_valid_int(cfg):
    cfg({"tasks": {"workers": 3}})
    assert get_int("tasks", "workers", 8) == 3


def test_absent_key_uses_default(cfg):
    cfg({})
    assert get_int("tasks", "workers", 8) == 8


def test_wrong_type_falls_back(cfg):
    cfg({"ai": {"max_iterations": "lots"}})
    assert get_int("ai", "max_iterations", 25) == 25


def test_below_minimum_falls_back(cfg):
    cfg({"ai": {"max_output_tokens": 10}})
    assert get_int("ai", "max_output_tokens", 64000, minimum=1024) == 64000


def test_default_minimum_is_one(cfg):
    cfg({"tasks": {"workers": 0}})
    assert get_int("tasks", "workers", 4) == 4  # 0 < default minimum of 1


def test_numeric_string_is_coerced(cfg):
    cfg({"logging": {"backup_count": "5"}})
    assert get_int("logging", "backup_count", 3, minimum=0) == 5
