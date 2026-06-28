import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator

from flask import Flask, Response, jsonify, make_response, redirect, render_template, request, stream_with_context, url_for
from flask_babel import gettext, ngettext

import yaffo.db.repositories.media_dir_repository
from yaffo.background_tasks.tasks.classify_labels_automation import classify_labels_automation_task
from yaffo.common import DB_PATH, QUEUE_DB_PATH
from yaffo.db import db
from yaffo.db.models import (
    AUTOMATION_HANDLER_CLASSIFY_LABELS,
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
    ApplicationSettings,
    ClassificationLabel,
    Face,
    Job,
    MediaItem,
)
from yaffo.db.repositories import automation_repository, classification_repository, media_repository
from yaffo.distance_units import get_saved_distance_unit, set_distance_unit
from yaffo.i18n import get_saved_locale, set_locale
from yaffo.site_agents import llm_config
from yaffo.utils.exiftool_path import get_exiftool_path
from yaffo.utils.face_analysis import get_model_location as get_face_model_location
from yaffo.utils.ffmpeg_path import get_ffmpeg_path
from yaffo.utils.image_classifier import get_model_location as get_classification_model_location
from yaffo.version import get_build_info


# NDJSON records the thumbnail-stats stream emits (one JSON object per line). Named so
# the page and the stream route agree on one shape; `type` discriminates them. Mirrors
# the scan stream in yaffo/routes/utilities/index_photos.py.
@dataclass
class ThumbnailStatsProgress:
    scanned: int
    type: str = "progress"


@dataclass
class ThumbnailStatsComplete:
    count: int
    size: int
    type: str = "done"


@dataclass
class ThumbnailStatsError:
    message: str
    code: str = "thumbnail_stats_failed"
    type: str = "error"


_LIBRARY_UPDATE_JOB_NAMES = ("import_photos", "index_photos")
_ACTIVE_JOB_STATUSES = (JOB_STATUS_PENDING, JOB_STATUS_RUNNING)


def _model_template_info(location):
    return {
        "path": str(location.path) if location.path else "",
        "source": location.source,
    }


def _relocated_thumbnail_path(path_value: str | None, current_dir: Path, new_dir: Path) -> str | None:
    if not path_value:
        return None

    path = Path(path_value)
    for old_base, new_base in ((current_dir, new_dir), (current_dir.resolve(), new_dir.resolve())):
        try:
            relative_path = path.relative_to(old_base)
        except ValueError:
            continue
        return str(new_base / relative_path)
    return None


def _library_update_in_progress() -> bool:
    return db.session.query(Job.id).filter(
        Job.name.in_(_LIBRARY_UPDATE_JOB_NAMES),
        Job.status.in_(_ACTIVE_JOB_STATUSES),
    ).first() is not None


def _library_update_blocked_response():
    return jsonify({
        "error": gettext("Media and thumbnail directories cannot be changed while importing or indexing is in progress."),
        "code": "library_update_in_progress",
    }), 409


def iter_thumbnail_stats(directory: Path | None, progress_every: int = 500) -> Iterator[int | tuple[int, int]]:
    """Walk the thumbnail dir, yielding the running file count every `progress_every`
    files (so a caller can drive a live counter), then the final (count, total_size).
    Mirrors iter_media_scan; the stream route consumes this, get_thumbnail_stats drains
    it for callers that just want the result."""
    count = 0
    total_size = 0
    if directory is None or not directory.exists():
        yield (count, total_size)
        return

    for file_path in directory.rglob("*"):
        if file_path.is_file():
            count += 1
            total_size += file_path.stat().st_size
            if count % progress_every == 0:
                yield count

    yield (count, total_size)


def init_settings_routes(app: Flask):
    def get_thumbnail_stats(directory: Path| None):
        """Get count and total size of thumbnails in directory (drains
        iter_thumbnail_stats for callers that want the result synchronously)."""
        count = 0
        total_size = 0
        try:
            for event in iter_thumbnail_stats(directory):
                if isinstance(event, tuple):
                    count, total_size = event
        except Exception as e:
            print(f"Error getting thumbnail stats: {e}")

        return count, total_size

    @app.route("/settings", methods=["GET"])
    def settings_index():
        media_dirs = yaffo.db.repositories.media_dir_repository.list_media_dirs(db.session)
        llm_status = llm_config.status()
        model_labels = {
            "claude-opus-4-8": gettext("Claude Opus 4.8 — most capable"),
            "claude-sonnet-4-6": gettext("Claude Sonnet 4.6 — balanced"),
            "claude-haiku-4-5-20251001": gettext("Claude Haiku 4.5 — fastest"),
        }
        llm_status["models"] = [
            {
                **model,
                "label": model_labels.get(model["id"], model["label"]),
            }
            for model in llm_status["models"]
        ]

        # Get thumbnail directory setting
        thumbnail_setting = db.session.query(ApplicationSettings).filter_by(name="thumbnail_dir").first()

        if thumbnail_setting and thumbnail_setting.value:
            current_thumbnail_dir = Path(thumbnail_setting.value)
        else:
            current_thumbnail_dir = None
        # Thumbnail stats (a slow recursive walk on large libraries) are filled in live
        # by the streaming endpoint below, so the page no longer blocks on counting them.
        exiftool_path = get_exiftool_path()
        ffmpeg_path = get_ffmpeg_path()
        library_update_in_progress = _library_update_in_progress()

        return render_template(
            "settings/index.html",
            media_dirs=media_dirs,
            library_update_in_progress=library_update_in_progress,
            db_path=str(DB_PATH),
            current_thumbnail_dir=str(current_thumbnail_dir) if current_thumbnail_dir else None,
            queue_db_path=str(QUEUE_DB_PATH),
            exiftool_path=str(exiftool_path) if exiftool_path else None,
            ffmpeg_path=str(ffmpeg_path) if ffmpeg_path else None,
            classification_model=_model_template_info(get_classification_model_location()),
            face_model=_model_template_info(get_face_model_location()),
            build_info=get_build_info(),
            llm=llm_status,
            labels=classification_repository.list_labels(db.session),
            selected_distance_unit=get_saved_distance_unit(db.session),
            selected_locale=get_saved_locale() or app.config["BABEL_DEFAULT_LOCALE"],
        )

    @app.route("/settings/locale", methods=["POST"])
    def settings_locale():
        if not set_locale(request.form.get("locale", "")):
            return jsonify({"error": "Unsupported locale"}), 400
        return redirect(url_for("settings_index"))

    @app.route("/settings/distance-unit", methods=["POST"])
    def settings_distance_unit():
        if not set_distance_unit(request.form.get("distance_unit", "")):
            return jsonify({"error": gettext("Unsupported distance unit")}), 400
        return redirect(url_for("settings_index"))

    def _render_labels():
        return render_template(
            "settings/_labels.html",
            labels=classification_repository.list_labels(db.session),
        )

    def _notify(message: str, type: str = "success"):
        """Empty 204 that fires the global showNotification toast (base.html), for
        HTMX actions whose only feedback is a notification — no DOM swap (HTMX skips
        the swap on 204, so the user's form input is preserved on error)."""
        response = make_response("", 204)
        response.headers["HX-Trigger"] = json.dumps({
            "showNotification": {"message": message, "type": type}
        })
        return response

    @app.route("/settings/labels", methods=["POST"])
    def settings_labels():
        """CRUD for the classification-label vocabulary, one HTMX endpoint keyed by
        `action`. Create/delete re-render the section fragment on success; validation
        errors return a toast (204, no swap, so typed input is kept). Toggle is a
        fire-and-forget that just persists (the browser already flipped the box), so
        it returns 204 and the panel isn't re-rendered."""
        action = request.form.get("action")
        if action == "toggle":
            label = db.session.get(ClassificationLabel, int(request.form["label_id"]))
            if label is not None:
                classification_repository.set_enabled(db.session, label.id, not label.enabled)
            return "", 204
        if action == "create":
            name = (request.form.get("name") or "").strip()
            prompt = (request.form.get("prompt") or "").strip()
            if not name:
                return _notify(gettext("Label name is required."), "error")
            if classification_repository.get_label_by_name(db.session, name):
                return _notify(
                    gettext("Label '%(name)s' already exists.", name=name),
                    "error",
                )
            classification_repository.create_label(db.session, name, prompt or None)
        elif action == "delete":
            classification_repository.delete_label(db.session, int(request.form["label_id"]))
        return _render_labels()

    @app.route("/settings/labels/reclassify", methods=["POST"])
    def settings_labels_reclassify():
        """Backfill: re-run the classifier over every indexed photo so vocabulary
        changes take effect. Enqueues the automation's task (recorded as a Job).
        Feedback is a toast (no section re-render)."""
        automation = automation_repository.get_by_slug(db.session, AUTOMATION_HANDLER_CLASSIFY_LABELS)
        if automation is None:
            return _notify(
                gettext("Classify-labels automation is not installed."),
                "error",
            )
        media_item_ids = media_repository.get_indexed_media_item_ids(db.session)
        if not media_item_ids:
            return _notify(
                gettext("No indexed photos to classify yet."),
                "error",
            )
        classify_labels_automation_task(automation.id, media_item_ids)
        count = len(media_item_ids)
        return _notify(ngettext(
            "Re-classifying %(count)s photo in the background…",
            "Re-classifying %(count)s photos in the background…",
            count,
            count=count,
        ))

    def _render_api_key(provider_id: str):
        return render_template(
            "settings/_llm_api_key.html", provider=llm_config.provider_status(provider_id))

    @app.route("/settings/llm/model", methods=["POST"])
    def settings_llm_model():
        # Persist the model and swap the single key box to that model's provider, with a
        # toast for confirmation. Only the key fragment is swapped (not the whole
        # section), so the model <select> keeps its state.
        llm_config.set_model((request.form.get("model") or "").strip())
        response = make_response(_render_api_key(llm_config.selected_model_provider()))
        response.headers["HX-Trigger"] = json.dumps({
            "showNotification": {
                "message": gettext("AI model updated."),
                "type": "success",
            }
        })
        return response

    @app.route("/settings/llm/api-key", methods=["POST"])
    def settings_llm_api_key():
        provider = (request.form.get("provider") or "").strip()
        key = (request.form.get("api_key") or "").strip()
        if provider and key:
            llm_config.set_api_key(provider, key)
        return _render_api_key(provider)

    @app.route("/settings/llm/api-key/clear", methods=["POST"])
    def settings_llm_api_key_clear():
        provider = (request.form.get("provider") or "").strip()
        if provider:
            llm_config.clear_api_key(provider)
        return _render_api_key(provider)

    @app.route("/api/settings/media-dirs", methods=["POST"])
    def add_media_dir():
        """Add a new media directory"""
        if _library_update_in_progress():
            return _library_update_blocked_response()

        data = request.get_json(silent=True) or {}
        new_dir = data.get("directory", "").strip()

        if not new_dir:
            return jsonify({
                "error": gettext("Directory path is required"),
                "code": "directory_path_required",
            }), 400

        try:
            Path(new_dir).mkdir(parents=True, exist_ok=True)
        except OSError:
            return jsonify({
                "error": gettext("Could not create the directory"),
                "code": "directory_create_failed",
            }), 400

        if yaffo.db.repositories.media_dir_repository.add_media_dir(db.session, new_dir) is None:
            return jsonify({
                "error": gettext("Directory is already configured"),
                "code": "directory_already_configured",
            }), 400

        return jsonify({
            "media_dirs": yaffo.db.repositories.media_dir_repository.list_media_dirs(db.session)
        })

    @app.route("/api/settings/media-dirs/<int:index>", methods=["DELETE"])
    def remove_media_dir(index: int):
        """Remove a media directory by index"""
        if _library_update_in_progress():
            return _library_update_blocked_response()

        removed = yaffo.db.repositories.media_dir_repository.remove_media_dir(db.session, index)
        if removed is None:
            return jsonify({
                "error": gettext("Media directory not found"),
                "code": "media_directory_not_found",
            }), 404
        return jsonify({
            "removed": removed,
            "media_dirs": yaffo.db.repositories.media_dir_repository.list_media_dirs(db.session),
        })

    @app.route("/api/settings/thumbnail-stats", methods=["GET"])
    def get_thumbnail_stats_api():
        """Get current thumbnail directory statistics"""
        # Get thumbnail_dir from settings or use default
        thumbnail_setting = db.session.query(ApplicationSettings).filter_by(name="thumbnail_dir").first()

        if thumbnail_setting and thumbnail_setting.value:
            thumbnail_dir = Path(thumbnail_setting.value)
        else:
            thumbnail_dir = None

        count, size = get_thumbnail_stats(thumbnail_dir)

        return jsonify({
            "directory": str(thumbnail_dir),
            "count": count,
            "size": size,
        })

    @app.route("/settings/thumbnail-stats/stream", methods=["GET"])
    def settings_thumbnail_stats_stream():
        """Stream the thumbnail-dir walk as NDJSON: `progress` records carry the running
        file count while it walks, then one `done` record with the final count + size.
        The page renders first and fills these stats live instead of blocking on the
        walk. Mirrors the scan stream in routes/utilities/index_photos.py."""
        thumbnail_setting = db.session.query(ApplicationSettings).filter_by(name="thumbnail_dir").first()
        thumbnail_dir = Path(thumbnail_setting.value) if thumbnail_setting and thumbnail_setting.value else None

        def generate():
            try:
                for event in iter_thumbnail_stats(thumbnail_dir):
                    if isinstance(event, tuple):
                        count, total_size = event
                        yield json.dumps(asdict(ThumbnailStatsComplete(
                            count=count,
                            size=total_size,
                        ))) + "\n"
                    else:
                        yield json.dumps(asdict(ThumbnailStatsProgress(scanned=event))) + "\n"
            except OSError:
                yield json.dumps(asdict(ThumbnailStatsError(
                    message=gettext("Could not count thumbnail files"),
                ))) + "\n"

        # no-store: live data, so the browser re-walks every load rather than caching.
        return Response(
            stream_with_context(generate()),
            mimetype="application/x-ndjson",
            headers={"Cache-Control": "no-store"},
        )

    @app.route("/api/settings/thumbnail-dir", methods=["POST"])
    def update_thumbnail_dir():
        """Update thumbnail directory and move files"""
        if _library_update_in_progress():
            return _library_update_blocked_response()

        data = request.get_json(silent=True) or {}
        new_dir = data.get("directory", "").strip()

        if not new_dir:
            return jsonify({
                "error": gettext("Directory path is required"),
                "code": "directory_path_required",
            }), 400

        new_dir_path = Path(new_dir)

        # Get current thumbnail directory
        thumbnail_setting = db.session.query(ApplicationSettings).filter_by(name="thumbnail_dir").first()

        if thumbnail_setting and thumbnail_setting.value:
            current_dir = Path(thumbnail_setting.value)
        else:
            current_dir = None

        # Check if new directory is the same as current
        if current_dir and new_dir_path.resolve() == current_dir.resolve():
            return jsonify({
                "error": gettext("The new directory is the same as the current directory"),
                "code": "thumbnail_directory_unchanged",
            }), 400

        try:
            # Create new directory if it doesn't exist
            new_dir_path.mkdir(parents=True, exist_ok=True)

            # Get stats before moving
            file_count, total_size = get_thumbnail_stats(current_dir)

            # Move files
            if current_dir and current_dir.exists() and file_count > 0:
                for file_path in current_dir.rglob("*"):
                    if file_path.is_file():
                        relative_path = file_path.relative_to(current_dir)
                        dest_path = new_dir_path / relative_path
                        dest_path.parent.mkdir(parents=True, exist_ok=True)

                        shutil.move(str(file_path), str(dest_path))

            # Update DB references by path prefix, not by exact moved filenames. This
            # keeps references correct even when a referenced crop was already missing
            # from disk or a stored path differs lexically from the rglob result.
            faces_updated = 0
            posters_updated = 0
            if current_dir:
                faces = db.session.query(Face).filter(Face.full_file_path.isnot(None)).all()
                for face in faces:
                    relocated = _relocated_thumbnail_path(face.full_file_path, current_dir, new_dir_path)
                    if relocated and relocated != face.full_file_path:
                        face.full_file_path = relocated
                        faces_updated += 1

                media_items = db.session.query(MediaItem).filter(MediaItem.poster_path.isnot(None)).all()
                for media_item in media_items:
                    relocated = _relocated_thumbnail_path(media_item.poster_path, current_dir, new_dir_path)
                    if relocated and relocated != media_item.poster_path:
                        media_item.poster_path = relocated
                        posters_updated += 1

            # Update or create setting
            if thumbnail_setting:
                thumbnail_setting.value = str(new_dir_path)
            else:
                thumbnail_setting = ApplicationSettings(
                    name="thumbnail_dir",
                    type="str",
                    value=str(new_dir_path)
                )
                db.session.add(thumbnail_setting)

            db.session.commit()

            return jsonify({
                "new_directory": str(new_dir_path),
                "files_moved": file_count,
                "faces_updated": faces_updated,
                "posters_updated": posters_updated,
                "size_moved": total_size,
            })

        except OSError:
            db.session.rollback()
            return jsonify({
                "error": gettext("Could not move thumbnail files"),
                "code": "thumbnail_move_failed",
            }), 500
