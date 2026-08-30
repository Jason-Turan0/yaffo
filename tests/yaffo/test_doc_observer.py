"""The dev-only observer that tells the guide automation which source files backed a
rendered page.

Two properties matter most: it must be completely absent unless switched on, and two
concurrent runs must never see each other's records.

The recorder is exercised directly rather than through a synthetic Flask app: paths
are filtered to this application's own source, so a throwaway app's routes and
templates are correctly recorded as nothing at all.
"""
import pytest
from flask import Flask

from yaffo import doc_observer
from yaffo.doc_observer import (
    ENV_FLAG,
    MAX_RUNS,
    OBSERVER_PREFIX,
    PAGE_HEADER,
    RUN_HEADER,
    UNATTRIBUTED,
    _Recorder,
    init_doc_observer,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def ctx_app():
    """A bare app, only so request contexts carrying the headers can be pushed."""
    return Flask(__name__)


def _record(app, recorder, *, run=None, page=None, routes=(), templates=()):
    headers = {}
    if run:
        headers[RUN_HEADER] = run
    if page:
        headers[PAGE_HEADER] = page
    with app.test_request_context("/", headers=headers):
        for path in routes:
            recorder.add("routes", path)
        for path in templates:
            recorder.add("templates", path)


class TestRecorder:
    def test_buckets_by_run_and_keeps_the_page_as_metadata(self, ctx_app):
        recorder = _Recorder()
        _record(ctx_app, recorder, run="r1", page="guide/example",
                routes=["yaffo/routes/home.py"], templates=["yaffo/templates/index.html"])
        taken = recorder.take("r1")
        assert taken["page"] == "guide/example"
        assert taken["routes"] == ["yaffo/routes/home.py"]
        assert taken["templates"] == ["yaffo/templates/index.html"]

    def test_two_runs_do_not_share_a_bucket(self, ctx_app):
        recorder = _Recorder()
        _record(ctx_app, recorder, run="run-a", page="page/a", routes=["yaffo/a.py"])
        _record(ctx_app, recorder, run="run-b", page="page/b", routes=["yaffo/b.py"])
        assert recorder.take("run-a")["routes"] == ["yaffo/a.py"]
        assert recorder.take("run-b")["routes"] == ["yaffo/b.py"]

    def test_collecting_one_run_leaves_the_other_intact(self, ctx_app):
        """Concurrency hinges on this. An earlier design reset globally, which would
        have had one walkthrough wipe another's in-flight records."""
        recorder = _Recorder()
        _record(ctx_app, recorder, run="run-a", page="a", routes=["yaffo/a.py"])
        _record(ctx_app, recorder, run="run-b", page="b", routes=["yaffo/b.py"])
        recorder.take("run-a")
        assert set(recorder.snapshot()) == {"run-b"}

    def test_a_run_is_consumed_when_read(self, ctx_app):
        recorder = _Recorder()
        _record(ctx_app, recorder, run="r1", page="p", routes=["yaffo/a.py"])
        assert recorder.take("r1") is not None
        assert recorder.take("r1") is None

    def test_unknown_run_reads_as_none(self):
        assert _Recorder().take("never-ran") is None

    def test_records_without_a_run_header_are_bucketed_not_dropped(self, ctx_app):
        recorder = _Recorder()
        _record(ctx_app, recorder, routes=["yaffo/a.py"])
        assert UNATTRIBUTED in recorder.snapshot()

    def test_empty_paths_are_ignored(self, ctx_app):
        """_relative returns None for anything outside the package; that must not
        create an empty bucket."""
        recorder = _Recorder()
        _record(ctx_app, recorder, run="r1", page="p", routes=[None, ""])
        assert recorder.snapshot() == {}

    def test_paths_are_deduplicated_and_sorted(self, ctx_app):
        recorder = _Recorder()
        _record(ctx_app, recorder, run="r1", page="p",
                routes=["yaffo/b.py", "yaffo/a.py", "yaffo/b.py"])
        assert recorder.take("r1")["routes"] == ["yaffo/a.py", "yaffo/b.py"]

    def test_old_runs_are_evicted_so_buckets_cannot_grow_without_limit(self, ctx_app):
        """A run that crashes before collecting must not leak a bucket forever."""
        recorder = _Recorder()
        for i in range(MAX_RUNS + 5):
            _record(ctx_app, recorder, run=f"run-{i}", page="p", routes=["yaffo/a.py"])
        snapshot = recorder.snapshot()
        assert len(snapshot) <= MAX_RUNS
        assert "run-0" not in snapshot, "oldest should be evicted first"
        assert f"run-{MAX_RUNS + 4}" in snapshot

    def test_snapshot_does_not_consume(self, ctx_app):
        recorder = _Recorder()
        _record(ctx_app, recorder, run="r1", page="p", routes=["yaffo/a.py"])
        recorder.snapshot()
        assert recorder.take("r1") is not None


class TestRegistration:
    def test_disabled_without_the_env_flag(self, monkeypatch):
        monkeypatch.delenv(ENV_FLAG, raising=False)
        app = Flask(__name__)
        cache_before = app.jinja_env.cache
        init_doc_observer(app)
        assert not [r for r in app.url_map.iter_rules() if OBSERVER_PREFIX in r.rule]
        # Leaving the template cache alone matters: disabling it is a real perf change.
        assert app.jinja_env.cache is cache_before

    def test_enabled_registers_endpoints_and_disables_the_template_cache(self, monkeypatch):
        monkeypatch.setenv(ENV_FLAG, "1")
        app = Flask(__name__)
        init_doc_observer(app)
        rules = {r.rule for r in app.url_map.iter_rules() if OBSERVER_PREFIX in r.rule}
        assert OBSERVER_PREFIX in rules
        # Without this the second page to render base.html never re-hits the loader.
        assert app.jinja_env.cache is None


class TestAgainstTheRealApp:
    """End to end through create_app, so the wiring in the factory is covered too."""

    @pytest.fixture
    def app(self, monkeypatch, tmp_path):
        monkeypatch.setenv(ENV_FLAG, "1")
        from yaffo.app import create_app
        from yaffo.db import db
        application = create_app(db_path=tmp_path / "test.db", config={"TESTING": True})
        with application.app_context():
            db.create_all()
            yield application
            db.session.remove()
            db.drop_all()

    def test_records_real_routes_and_included_templates(self, app):
        client = app.test_client()
        client.get("/", headers={RUN_HEADER: "r1", PAGE_HEADER: "library-basics/browsing-filtering"})
        taken = client.get(f"{OBSERVER_PREFIX}/r1").get_json()

        assert taken["page"] == "library-basics/browsing-filtering"
        assert "yaffo/routes/home.py" in taken["routes"]
        assert "yaffo/templates/index.html" in taken["templates"]
        # Flask's template_rendered signal fires only for the top-level render, so
        # these would be missed; wrapping the loader is what catches them.
        assert "yaffo/templates/_sidebar.html" in taken["templates"]
        assert "yaffo/templates/components/photo_card.html" in taken["templates"]

    def test_no_dependency_is_recorded_from_outside_the_package(self, app):
        """Flask's own static handler lives in site-packages and must never be
        recorded as a dependency of every page."""
        client = app.test_client()
        client.get("/", headers={RUN_HEADER: "r1", PAGE_HEADER: "p"})
        taken = client.get(f"{OBSERVER_PREFIX}/r1").get_json()
        recorded = taken["routes"] + taken["templates"]
        assert recorded, "expected the page to record something"
        assert all(path.startswith("yaffo/") for path in recorded)
        assert not any("site-packages" in path or "venv" in path for path in recorded)

    def test_unknown_run_is_404_not_an_empty_success(self, app):
        """404 distinguishes "no such run" from "this run touched no yaffo source"."""
        assert app.test_client().get(f"{OBSERVER_PREFIX}/never-ran").status_code == 404

    def test_the_observer_does_not_record_itself(self, app):
        client = app.test_client()
        client.get("/", headers={RUN_HEADER: "r1", PAGE_HEADER: "p"})
        client.get(OBSERVER_PREFIX, headers={RUN_HEADER: "r1"})
        routes = client.get(f"{OBSERVER_PREFIX}/r1").get_json()["routes"]
        assert not any("doc_observer" in r for r in routes)


class TestPathFiltering:
    """Only this application's source counts. A virtualenv usually lives at the repo
    root, so a hop-counted root would admit Flask's own static handler as a
    dependency of every page — and when the checkout is itself named `yaffo`, a
    startswith guard passes for the wrong reason and hides it."""

    def test_package_source_is_repo_relative(self):
        assert doc_observer._relative(doc_observer.__file__) == "yaffo/doc_observer.py"

    def test_site_packages_is_excluded(self):
        import flask
        assert doc_observer._relative(flask.__file__) is None

    def test_paths_outside_the_checkout_are_excluded(self):
        assert doc_observer._relative("/usr/lib/python3.13/os.py") is None

    def test_missing_path_is_excluded(self):
        assert doc_observer._relative(None) is None
