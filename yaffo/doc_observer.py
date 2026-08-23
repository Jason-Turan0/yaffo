"""Records which source files back a rendered page, for the user-guide automation.

A browser can see that ``GET /`` returned HTML. It cannot see that Flask dispatched
to ``home.index`` in ``yaffo/routes/home.py``, or that rendering pulled in
``index.html``, ``_sidebar.html``, and ``components/photo_card.html``. Those facts
only exist inside this process, so the walkthrough runner asks for them here.

Development-only: enable with ``YAFFO_DOC_OBSERVER=1``. With the variable unset
``init_doc_observer`` returns immediately and the app is untouched, so nothing here
is reachable in a shipped configuration.

Requests are attributed by two headers the runner sets on the browser context:
``X-Yaffo-Doc-Run`` (a fresh id per walkthrough run) buckets the records, and
``X-Yaffo-Doc-Page`` rides along as metadata. Bucketing by run rather than by page
means two runs never share a bucket, so reading one out cannot disturb another and
there is no global reset to race against. Anything without a run header lands in the
``_unattributed`` bucket rather than being dropped, so a missing header shows up as
a visible oddity instead of a silently short dependency set.

See yaffo_ui_tests/docs/documentation_automation.md ("The observed-dependency
lockfile").
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from flask import Flask, has_request_context, jsonify, request
from jinja2 import BaseLoader

from yaffo.logging_config import get_logger

logger = get_logger(__name__, "webapp")

ENV_FLAG = "YAFFO_DOC_OBSERVER"
PAGE_HEADER = "X-Yaffo-Doc-Page"
RUN_HEADER = "X-Yaffo-Doc-Run"
UNATTRIBUTED = "_unattributed"
# Runs are consumed when read, so buckets normally clear themselves. This bounds the
# damage from a run that crashes before collecting: oldest buckets are evicted rather
# than growing without limit in a long-lived dev server.
MAX_RUNS = 64
# Longest accepted run id, so a stray header cannot allocate an unbounded key.
MAX_RUN_ID = 128
# Prefix for this module's own endpoints, which must never appear in a page's
# dependency set.
OBSERVER_PREFIX = "/__doc_observer__"

# Anchored on this package rather than by counting parent hops, so moving this
# module between directories cannot silently shift what "repo root" means. The
# observer is dev-only, so assuming a source checkout is safe here.
_PACKAGE_ROOT = Path(__file__).resolve().parent          # .../yaffo
_REPO_ROOT = _PACKAGE_ROOT.parent                        # the checkout


def _relative(path: str | Path | None) -> Optional[str]:
    """Repo-relative path, or None if the file is not this application's source.

    Membership is tested against the package directory, not merely "inside the
    checkout": a virtualenv usually lives at the repo root, so Flask's own built-in
    static handler resolves to ``venv/lib/.../flask/app.py`` and would otherwise be
    recorded as a dependency of every page.
    """
    if not path:
        return None
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(_PACKAGE_ROOT):
        return None
    return str(resolved.relative_to(_REPO_ROOT))


class _Recorder:
    """Per-run sets of source files, guarded for the threaded dev server."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # Insertion-ordered, so evicting the oldest run is just the first key.
        self._runs: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _current() -> tuple[str, str]:
        """(run id, page id) for the request being served."""
        if not has_request_context():
            return UNATTRIBUTED, UNATTRIBUTED
        run = (request.headers.get(RUN_HEADER) or "")[:MAX_RUN_ID] or UNATTRIBUTED
        return run, request.headers.get(PAGE_HEADER) or UNATTRIBUTED

    def add(self, kind: str, path: Optional[str]) -> None:
        if not path:
            return
        run, page = self._current()
        with self._lock:
            bucket = self._runs.get(run)
            if bucket is None:
                while len(self._runs) >= MAX_RUNS:
                    self._runs.pop(next(iter(self._runs)))
                bucket = self._runs[run] = {"page": page, "routes": set(), "templates": set()}
            bucket[kind].add(path)

    @staticmethod
    def _rendered(bucket: dict[str, Any]) -> dict[str, Any]:
        return {
            "page": bucket["page"],
            "routes": sorted(bucket["routes"]),
            "templates": sorted(bucket["templates"]),
        }

    def take(self, run: str) -> Optional[dict[str, Any]]:
        """Read one run's records and drop them.

        Destructive by design: a run collects exactly once, so consuming removes the
        need for a reset call that would otherwise clear every concurrent run's
        bucket too.
        """
        with self._lock:
            bucket = self._runs.pop(run, None)
        return self._rendered(bucket) if bucket else None

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """Everything currently held, without consuming. For debugging only."""
        with self._lock:
            return {run: self._rendered(bucket) for run, bucket in self._runs.items()}


class _RecordingLoader(BaseLoader):
    """Delegating loader that records every template actually loaded.

    Deliberately not Flask's ``template_rendered`` signal: that fires only for
    top-level ``render_template`` calls, so includes, imports, and inheritance
    parents never emit it — and those partials are the ones most likely to change.
    Every one of them goes through ``get_source``.
    """

    def __init__(self, inner: BaseLoader, record: Callable[[Optional[str]], None]) -> None:
        self._inner = inner
        self._record = record

    def get_source(self, environment: Any, template: str) -> tuple[str, Optional[str], Any]:
        source, filename, uptodate = self._inner.get_source(environment, template)
        # The resolved filename beats the template name: it survives any loader
        # that maps a name to somewhere other than yaffo/templates.
        self._record(_relative(filename))
        return source, filename, uptodate

    def list_templates(self) -> list[str]:
        return self._inner.list_templates()


def init_doc_observer(app: Flask) -> None:
    """Register the observer when YAFFO_DOC_OBSERVER=1. Otherwise a no-op."""
    if os.environ.get(ENV_FLAG, "").strip() not in {"1", "true", "True"}:
        return

    recorder = _Recorder()

    if app.jinja_env.loader is not None:
        app.jinja_env.loader = _RecordingLoader(
            app.jinja_env.loader, lambda path: recorder.add("templates", path)
        )
    # Jinja caches compiled templates, so without this the second page to render
    # base.html never re-hits the loader and its record comes back short. `cache`
    # is the live attribute; assigning `cache_size` after construction does nothing.
    app.jinja_env.cache = None

    @app.after_request
    def _record_endpoint(response):
        endpoint = request.endpoint
        if endpoint and not request.path.startswith(OBSERVER_PREFIX):
            view = app.view_functions.get(endpoint)
            module = sys.modules.get(view.__module__) if view else None
            recorder.add("routes", _relative(getattr(module, "__file__", None)))
        return response

    # GET so they sidestep CSRF; this is a dev diagnostic that never exists in a
    # shipped configuration.
    @app.route(f"{OBSERVER_PREFIX}")
    def doc_observer_snapshot():
        """Every run currently held. Non-destructive; for eyeballing during development."""
        return jsonify({"runs": recorder.snapshot()})

    @app.route(f"{OBSERVER_PREFIX}/<run_id>")
    def doc_observer_take(run_id: str):
        """One run's records, consumed. 404 when the run recorded nothing, which
        distinguishes "no such run" from "this run touched no yaffo source"."""
        taken = recorder.take(run_id[:MAX_RUN_ID])
        if taken is None:
            return jsonify({"error": "unknown run", "run": run_id}), 404
        return jsonify(taken)

    logger.info("doc observer enabled at %s", OBSERVER_PREFIX)
