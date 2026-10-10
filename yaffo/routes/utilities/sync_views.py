"""The sync automation pages: setup sections, status line, consent, Items, Test.

A sync is a type of automation, so it lives on the automation detail URL; this
module supplies its view model and the routes its sections post to. Every setup
change goes through yaffo.sync.setup, which clears the consent whenever something the
consent covers changes. See docs/development/automation-sync-adapters.md (UI).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from flask import Flask, abort, jsonify, redirect, render_template, request, url_for
from flask_babel import gettext, ngettext
from sqlalchemy.orm import Session

from yaffo.background_tasks.automation_dispatch import invoke_automation
from yaffo.db import db
from yaffo.db.models import (
    Album, Automation, MediaItem, SyncItem,
    EVENT_ALBUM_CHANGED, EVENT_MEDIA_DELETED, SYNC_HANDLER_FOLDER_TARGET, SYNC_ITEM_FAILED, SYNC_ROLE_SOURCE,
    SYNC_ROLE_TARGET, TRIGGER_TYPE_EVENT, TRIGGER_TYPE_SCHEDULE,
)
from yaffo.db.repositories import album_repository, media_dir_repository, sync_repository
from yaffo.db.repositories import automation_repository as repo
from yaffo.db.repositories.media_filter_repository import apply_media_filters
from yaffo.domain.media_filter_params import selections_from_wire
from yaffo.routes import filter_config
from yaffo.routes.filter_panel import build_filters_context, to_media_filters, to_query_params, to_wire_filters
from yaffo.routes.utilities import sync_text
from yaffo.routes.utilities.common import automations_sidebar_context
from yaffo.sync import guards, setup
from yaffo.sync.adapters import AdapterUnavailable, LoadedAdapter, runnable_adapters
from yaffo.sync.consent import has_consent
from yaffo.sync.folder_target import CONVERT_FRAME, CONVERT_OFF, ON_LEAVE_KEEP
from yaffo.sync.options import OPTION_BOOL, OPTION_LOCAL_FOLDER, OptionError
from yaffo.sync.preview import preview_sync
from yaffo.sync.schemas import (
    RUNTIME_NEEDS_CONSENT, RUNTIME_READY, SELECTION_ALBUM, SELECTION_QUERY, Selection, SyncColumn,
    SyncSchemaError,
)
from yaffo.sync.target_run import ITEM_NOT_SENT
from yaffo.utils import job_codes as codes

ITEMS_PAGE_SIZE = 25
STEP_DONE = "done"
STEP_TODO = "todo"
STEP_LOCKED = "locked"


@dataclass(frozen=True)
class OptionField:
    key: str
    type: str
    label: str
    help: str | None
    required: bool
    value: Any
    choices: list[dict]
    problem: str | None = None


@dataclass(frozen=True)
class ItemRow:
    id: int
    label: str
    status: str
    status_label: str
    error: str | None
    target: str | None
    remote_id: str | None
    media_item_id: int | None
    sent_as: str | None


@dataclass
class SyncPage:
    automation: Automation
    adapter: LoadedAdapter
    column: SyncColumn
    role: str
    status_text: str
    status_chip: str
    options: list[OptionField]
    options_done: bool
    where_done: bool
    albums: list[Album] = field(default_factory=list)
    filter_match_count: int | None = None
    filter_summary: str | None = None
    media_dirs: list[dict] = field(default_factory=list)
    import_root_path: str | None = None
    triggers: list[TriggerSummary] = field(default_factory=list)
    consent_terms: list[ConsentTerm] = field(default_factory=list)
    missing_labels: list[str] = field(default_factory=list)
    consented: bool = False
    missing: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    folder_target: bool = False
    summary: str | None = None
    options_summary: str | None = None
    where_summary: str | None = None
    status: str = RUNTIME_READY
    attention: bool = False

    @property
    def selection(self) -> Selection | None:
        return self.column.config.selection

    @property
    def can_turn_on(self) -> bool:
        return not self.missing and not (self.consented and self.automation.enabled)


def display_path(path: str | None) -> str | None:
    """A path as people read it: the home folder shown as ~."""
    if not path:
        return path
    home = str(Path.home())
    if path == home or path.startswith(home + "/") or path.startswith(home + "\\"):
        return "~" + path[len(home):]
    return path


def _option_label(text: str) -> str:
    return gettext(text) if text else text


def _option_fields(adapter: LoadedAdapter, values: dict, session: Session) -> list[OptionField]:
    fields = []
    for key, spec in adapter.options.items():
        value = values.get(key)
        problem = None
        if spec["type"] == OPTION_LOCAL_FOLDER and value:
            check = (guards.folder_source_problem if adapter.role == SYNC_ROLE_SOURCE
                     else guards.folder_target_problem)
            problem = sync_text.guard_text(check(session, Path(value)))
        fields.append(OptionField(
            key=key, type=spec["type"], label=_option_label(spec.get("label", key)),
            help=_option_label(spec["help"]) if spec.get("help") else None,
            required=bool(spec.get("required")), value=value,
            choices=[{"value": c["value"], "label": _option_label(c["label"])} for c in spec.get("choices", [])],
            problem=problem,
        ))
    return fields


def _matching_count(session: Session, query: dict | None) -> int | None:
    if not query:
        return None
    return apply_media_filters(session, session.query(MediaItem.id), selections_from_wire(query)).count()


def _import_root_path(session: Session, import_root: dict | None) -> str | None:
    if not import_root:
        return None
    root, _ = guards.resolve_import_root(session, import_root)
    return str(root) if root else None


@dataclass(frozen=True)
class TriggerSummary:
    """One enabled trigger, for the When section. A schedule's cron is described in
    the browser (the cron builder's describeCron), like on the triggers screen."""
    kind: str
    cron: str | None = None
    text: str | None = None


def _triggers(session: Session, automation: Automation) -> list[TriggerSummary]:
    summaries = []
    for trigger in sorted(automation.triggers, key=lambda t: t.id):
        if not trigger.enabled:
            continue
        if trigger.trigger_type == TRIGGER_TYPE_SCHEDULE:
            summaries.append(TriggerSummary(kind=TRIGGER_TYPE_SCHEDULE, cron=trigger.cron))
        elif trigger.event_type == EVENT_ALBUM_CHANGED:
            summaries.append(TriggerSummary(kind=TRIGGER_TYPE_EVENT,
                                            text=gettext("When the album changes (after a minute's quiet)")))
        elif trigger.event_type == EVENT_MEDIA_DELETED:
            summaries.append(TriggerSummary(kind=TRIGGER_TYPE_EVENT,
                                            text=gettext("When a sent photo is deleted from Yaffo")))
        else:
            summaries.append(TriggerSummary(kind=TRIGGER_TYPE_EVENT, text=trigger.event_type))
    return summaries


@dataclass(frozen=True)
class ConsentTerm:
    """One line of what the user agrees to when turning a sync on."""
    label: str
    value: str


def consent_terms(session: Session, page_column: SyncColumn, adapter: LoadedAdapter) -> list[ConsentTerm]:
    """What the sync will do, line by line, for the review before turning it on.
    Every line is covered by the consent hash: changing any of them turns the sync off."""
    config = page_column.config
    options = config.options
    if config.role == SYNC_ROLE_SOURCE:
        root = _import_root_path(session, config.import_root)
        if not options.get("folder") or not root:
            return []
        return [
            ConsentTerm(gettext("Copies new photos from"), display_path(adapter.handler.describe_source(options))),
            ConsentTerm(gettext("Into"), gettext("%(root)s, in a folder per year and month taken",
                                                 root=display_path(root))),
            ConsentTerm(gettext("Already in your library"), gettext("Linked, not copied again")),
            ConsentTerm(gettext("The originals"), gettext("Never changed or deleted")),
        ]
    if not options.get("folder") or config.selection is None:
        return []
    if config.selection.kind == SELECTION_ALBUM:
        album = session.get(Album, config.selection.album_id)
        what = gettext("The photos in the album %(album)s", album=album.name if album else "?")
    else:
        what = gettext("The photos matching your filter")
    convert = options.get("convert", CONVERT_OFF)
    form = (gettext("JPEG copies, sized for a photo frame") if convert == CONVERT_FRAME
            else gettext("JPEG copies") if convert != CONVERT_OFF else gettext("Copies of the originals"))
    if options.get("on_leave") == ON_LEAVE_KEEP:
        leave = gettext("Its copy stays in the folder")
    else:
        leave = gettext("Yaffo deletes its copy, to the trash (only copies it wrote and nobody changed)")
    return [
        ConsentTerm(gettext("Sends"), what),
        ConsentTerm(gettext("To"), display_path(adapter.handler.describe_destination(options))),
        ConsentTerm(gettext("As"), form),
        ConsentTerm(gettext("If a photo leaves or is deleted"), leave),
    ]


def missing_labels(page_missing: list[str], role: str) -> list[str]:
    """What still has to be done before the sync can be turned on, in words."""
    labels = []
    for code in page_missing:
        if code == "import_root":
            text = gettext("Choose where in your library the photos go")
        elif code == "selection":
            text = gettext("Choose what to send")
        elif code == "account":
            text = gettext("Sign in")
        elif code in ("option:folder", "folder"):
            text = (gettext("Choose a folder to copy from") if role == SYNC_ROLE_SOURCE
                    else gettext("Choose a folder outside your library"))
        else:
            text = gettext("Finish the folder and options")
        if text not in labels:
            labels.append(text)
    return labels


def build_page(session: Session, automation: Automation) -> SyncPage | None:
    try:
        column = sync_repository.load_sync(automation)
        adapter = setup.adapter_of(session, automation)
    except (SyncSchemaError, AdapterUnavailable):
        return None
    config, runtime = column.config, column.runtime
    options = _option_fields(adapter, config.options, session)
    options_done = all(o.value not in (None, "") or not o.required for o in options) and \
        not any(o.problem for o in options)
    consented = has_consent(config, adapter)
    status = runtime.status if consented or runtime.status != RUNTIME_READY else RUNTIME_NEEDS_CONSENT
    chip = ("chip-success" if automation.enabled and status == RUNTIME_READY else
            "chip-warning" if status != RUNTIME_READY else "")
    page = SyncPage(
        automation=automation, adapter=adapter, column=column, role=config.role,
        status_text=sync_text.runtime_text(status, runtime.pause_code, automation.enabled),
        status_chip=chip, options=options, options_done=options_done,
        where_done=bool(config.import_root if config.role == SYNC_ROLE_SOURCE else config.selection),
        triggers=_triggers(session, automation),
        consent_terms=consent_terms(session, column, adapter),
        consented=consented,
        missing=setup.missing_setup(session, automation) + [o.key for o in options if o.problem],
        counts=sync_repository.status_counts(session, automation.id),
        folder_target=adapter.handler_key == SYNC_HANDLER_FOLDER_TARGET,
    )
    if config.role == SYNC_ROLE_TARGET:
        page.albums = album_repository.list_albums(session)
        if config.selection and config.selection.kind == SELECTION_QUERY:
            page.filter_match_count = _matching_count(session, config.selection.query)
    else:
        page.media_dirs = media_dir_repository.list_media_dirs(session)
        page.import_root_path = _import_root_path(session, config.import_root)
    page.missing_labels = missing_labels(page.missing, config.role)
    page.status = status
    page.attention = status != RUNTIME_READY and status != RUNTIME_NEEDS_CONSENT
    page.options_summary = _options_summary(options)
    page.where_summary = _where_summary(session, page)
    page.summary = _summary(page)
    return page


def _options_summary(options: list[OptionField]) -> str | None:
    """The options in one line, for the collapsed section: the folder, then each
    choice's label."""
    parts = []
    for option in options:
        if option.type == OPTION_LOCAL_FOLDER and option.value:
            parts.append(display_path(option.value))
        elif option.choices:
            label = next((c["label"] for c in option.choices if c["value"] == option.value), None)
            if label:
                parts.append(label)
        elif option.type == OPTION_BOOL and option.value:
            parts.append(option.label)
    return " · ".join(parts) or None


def _where_summary(session: Session, page: SyncPage) -> str | None:
    config = page.column.config
    if page.role == SYNC_ROLE_SOURCE:
        return display_path(page.import_root_path)
    if config.selection is None:
        return None
    if config.selection.kind == SELECTION_ALBUM:
        album = session.get(Album, config.selection.album_id)
        return gettext("The album %(album)s", album=album.name) if album else gettext("A deleted album")
    return ngettext("Photos matching a filter (%(num)d now)", "Photos matching a filter (%(num)d now)",
                    page.filter_match_count or 0)


def _summary(page: SyncPage) -> str | None:
    """A short description of what the sync does for the page header."""
    folder = display_path(next((o.value for o in page.options if o.type == OPTION_LOCAL_FOLDER), None))
    if page.role == SYNC_ROLE_SOURCE:
        if folder:
            return gettext("Imports new photos from %(source)s", source=folder)
        return gettext("Copies new photos from a folder into your library.")
    if folder and page.where_summary:
        return gettext("%(what)s → %(folder)s", what=page.where_summary, folder=folder)
    return gettext("Keeps an album or a filter's photos copied to a folder.")


def can_enable(session: Session, automation: Automation) -> bool:
    page = build_page(session, automation)
    return page is not None and page.consented and not page.missing


def item_rows(session: Session, automation_id: int, status: str | None, page: int,
              folder_target: bool) -> list[ItemRow]:
    rows = sync_repository.items_page(session, automation_id, status, (page - 1) * ITEMS_PAGE_SIZE,
                                      ITEMS_PAGE_SIZE)
    media = {m.id: m for m in session.query(MediaItem).filter(
        MediaItem.id.in_([r.media_item_id for r in rows if r.media_item_id])).all()}
    result = []
    for row in rows:
        item = media.get(row.media_item_id) if row.media_item_id else None
        label = Path(item.full_file_path).name if item else (row.remote_id or row.item_key)
        sent_as = (row.details or {}).get("sent_as")
        result.append(ItemRow(
            id=row.id, label=label, status=row.status,
            status_label=sync_text.item_status_label(row.status, folder_target),
            error=sync_text.item_error_text(row.error_code), target=row.target, remote_id=row.remote_id,
            media_item_id=row.media_item_id, sent_as=gettext("sent as JPEG") if sent_as else None,
        ))
    return result


def _require_sync(slug: str) -> Automation:
    automation = repo.get_by_slug(db.session, slug)
    if automation is None or not automation.is_sync:
        abort(404)
    return automation


def _back(automation: Automation, error: str | None = None):
    if error:
        return redirect(url_for("automations_show", slug=automation.slug, sync_error=error))
    return redirect(url_for("automations_show", slug=automation.slug))


def create_from_form(session: Session, name: str, kind: str, adapter_id: str | None) -> Automation | None:
    """A new sync from the New automation dialog's Import/Send choice."""
    role = {"import": SYNC_ROLE_SOURCE, "send": SYNC_ROLE_TARGET}.get(kind)
    if role is None:
        return None
    adapters = {a.id: a for a in runnable_adapters(session, role)}
    try:
        adapter = adapters[int(adapter_id or 0)]
    except (KeyError, ValueError):
        return None
    automation = setup.create_sync(session, name, adapter.id)
    if role == SYNC_ROLE_SOURCE:
        dirs = media_dir_repository.list_media_dirs(session)
        if dirs:
            setup.set_import_root(session, automation, dirs[0]["id"], setup.default_import_subpath(name))
    return automation


def init_sync_routes(app: Flask) -> None:
    @app.route("/utilities/automations/<slug>/sync/options", methods=["POST"])
    def sync_update_options(slug: str):
        automation = _require_sync(slug)
        adapter = setup.adapter_of(db.session, automation)
        values = {}
        for key, spec in adapter.options.items():
            if spec["type"] == OPTION_BOOL:
                values[key] = key in request.form
            elif key in request.form:
                values[key] = request.form.get(key, "").strip()
        try:
            setup.set_options(db.session, automation, values)
        except OptionError as e:
            return _back(automation, error=e.code)
        return _back(automation)

    @app.route("/utilities/automations/<slug>/sync/selection", methods=["POST"])
    def sync_update_selection(slug: str):
        automation = _require_sync(slug)
        kind = request.form.get("kind")
        if kind == SELECTION_ALBUM:
            album_id = request.form.get("album_id", type=int)
            if album_id is None or db.session.get(Album, album_id) is None:
                return _back(automation, error="album_required")
            setup.set_selection(db.session, automation, Selection(kind=SELECTION_ALBUM, album_id=album_id))
        elif kind == SELECTION_QUERY:
            filters = build_filters_context(db.session, request.args)
            wire = to_wire_filters(filters)
            if not wire:
                return _back(automation, error="filter_required")
            setup.set_selection(db.session, automation, Selection(kind=SELECTION_QUERY, query=wire))
        else:
            abort(400)
        return _back(automation)

    @app.route("/utilities/automations/<slug>/sync/filter", methods=["GET"])
    def sync_filter(slug: str):
        """Choose "photos matching a filter": the library's filter panel, the number
        of photos it matches, and a preview of the first ones."""
        automation = _require_sync(slug)
        filters = build_filters_context(db.session, request.args)
        query = apply_media_filters(db.session, db.session.query(MediaItem), to_media_filters(filters))
        total = query.count()
        preview = query.order_by(MediaItem.date_taken.desc()).limit(24).all()
        return render_template(
            "utilities/sync_filter.html", selected=automation, filters=filters, match_count=total,
            preview=preview, filter_params=to_query_params(filters),
            filter_layout=filter_config.load_layout(db.session), filter_default_keys=filter_config.default_keys(),
            has_filters=bool(to_wire_filters(filters)),
        )

    @app.route("/utilities/automations/<slug>/sync/import-root", methods=["POST"])
    def sync_update_import_root(slug: str):
        automation = _require_sync(slug)
        media_dir_id = request.form.get("media_dir_id") or ""
        subpath = request.form.get("subpath") or ""
        _, problem = guards.resolve_import_root(db.session, {"media_dir_id": media_dir_id, "subpath": subpath})
        if problem and problem != guards.PROBLEM_MEDIA_DIR_MISSING:
            return _back(automation, error=problem)
        setup.set_import_root(db.session, automation, media_dir_id, subpath)
        return _back(automation)

    @app.route("/utilities/automations/<slug>/sync/turn-on", methods=["POST"])
    def sync_turn_on(slug: str):
        automation = _require_sync(slug)
        page = build_page(db.session, automation)
        if page is None or page.missing:
            return _back(automation, error="setup_incomplete")
        setup.turn_on(db.session, automation)
        return _back(automation)

    @app.route("/utilities/automations/<slug>/sync/turn-off", methods=["POST"])
    def sync_turn_off(slug: str):
        setup.turn_off(db.session, _require_sync(slug))
        return _back(_require_sync(slug))

    @app.route("/utilities/automations/<slug>/sync/items", methods=["GET"])
    def sync_items(slug: str):
        automation = _require_sync(slug)
        status = request.args.get("status") or None
        page_number = max(1, request.args.get("page", default=1, type=int))
        folder_target = setup.adapter_of(db.session, automation).handler_key == SYNC_HANDLER_FOLDER_TARGET
        return render_template(
            "utilities/sync_items.html", selected=automation, status=status, page=page_number,
            rows=item_rows(db.session, automation.id, status, page_number, folder_target),
            counts=sync_repository.status_counts(db.session, automation.id), page_size=ITEMS_PAGE_SIZE,
            folder_target=folder_target, status_label=sync_text.item_status_label,
        )

    @app.route("/utilities/automations/<slug>/sync/items/retry", methods=["POST"])
    def sync_retry_failed(slug: str):
        """Make failed items eligible again (including ones the target refused) and
        start a run."""
        automation = _require_sync(slug)
        db.session.query(SyncItem).filter(
            SyncItem.automation_id == automation.id, SyncItem.status == SYNC_ITEM_FAILED,
        ).update({SyncItem.error_code: ITEM_NOT_SENT}, synchronize_session=False)
        db.session.commit()
        if automation.enabled:
            invoke_automation(automation, None)
        return _back(automation)

    @app.route("/utilities/automations/<slug>/sync/test", methods=["POST"])
    def sync_test(slug: str):
        automation = _require_sync(slug)
        payload = asdict(preview_sync(db.session, automation))
        payload["problem_text"] = _preview_problem_text(payload["problem"])
        return jsonify(payload)


def render_sync_page(automation: Automation, recent_runs: list, trigger_context: dict) -> str:
    page = build_page(db.session, automation)
    if page is None:
        abort(500)
    return render_template(
        "utilities/sync_detail.html", selected=automation, selected_slug=automation.slug, sync=page,
        recent_runs=recent_runs, sync_error=request.args.get("sync_error"),
        sync_error_text=_error_text(request.args.get("sync_error")),
        **trigger_context,
        **automations_sidebar_context(),
    )


def _preview_problem_text(code: str | None) -> str | None:
    if not code:
        return None
    if code == "setup_incomplete":
        return gettext("Finish the setup first.")
    if code == "invalid_config":
        return gettext("This sync's setup can't be used.")
    if code == codes.OUTCOME_SYNC_WAITING_FOR_FOLDER:
        return gettext("The folder isn't connected.")
    return sync_text.pause_text(code)


def _error_text(code: str | None) -> str | None:
    if not code:
        return None
    return {
        "album_required": gettext("Choose an album."),
        "filter_required": gettext("Set at least one filter first."),
        "setup_incomplete": gettext("Finish the setup first."),
        "required": gettext("Fill in the required fields."),
        "not_absolute": gettext("Choose a folder with the folder picker."),
    }.get(code) or sync_text.guard_text(code)
