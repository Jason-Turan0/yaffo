from flask import Flask, render_template, request, jsonify, make_response, Response, stream_with_context, redirect, url_for

import yaffo.db.repositories.media_dir_repository
from yaffo.db import db
from yaffo.db.models import ApplicationSettings, Face, ClassificationLabel, AUTOMATION_HANDLER_CLASSIFY_LABELS
from yaffo.common import DB_PATH, QUEUE_DB_PATH
from yaffo.i18n import get_saved_locale, set_locale
from yaffo.version import get_build_info
from yaffo.site_agents import llm_config
import json
import subprocess
import platform
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator

from yaffo.background_tasks.tasks.classify_labels_automation import classify_labels_automation_task
from yaffo.db.repositories import automation_repository, classification_repository, media_repository
from yaffo.utils import settings as media_settings


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
    size_formatted: str
    type: str = "done"


@dataclass
class ThumbnailStatsError:
    message: str
    type: str = "error"


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

    def format_size(bytes_size: int) -> str:
        """Format bytes to human readable size"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if bytes_size < 1024.0:
                return f"{bytes_size:.2f} {unit}"
            bytes_size /= 1024.0
        return f"{bytes_size:.2f} TB"

    @app.route("/settings", methods=["GET"])
    def settings_index():
        media_dirs = yaffo.db.repositories.media_dir_repository.list_media_dirs(db.session)

        # Get thumbnail directory setting
        thumbnail_setting = db.session.query(ApplicationSettings).filter_by(name="thumbnail_dir").first()

        if thumbnail_setting and thumbnail_setting.value:
            current_thumbnail_dir = Path(thumbnail_setting.value)
        else:
            current_thumbnail_dir = None
        # Thumbnail stats (a slow recursive walk on large libraries) are filled in live
        # by the streaming endpoint below, so the page no longer blocks on counting them.

        return render_template(
            "settings/index.html",
            media_dirs=media_dirs,
            db_path=str(DB_PATH),
            current_thumbnail_dir=str(current_thumbnail_dir) if current_thumbnail_dir else None,
            queue_db_path=str(QUEUE_DB_PATH),
            build_info=get_build_info(),
            llm=llm_config.status(),
            labels=classification_repository.list_labels(db.session),
            selected_locale=get_saved_locale() or app.config["BABEL_DEFAULT_LOCALE"],
        )

    @app.route("/settings/locale", methods=["POST"])
    def settings_locale():
        if not set_locale(request.form.get("locale", "")):
            return jsonify({"error": "Unsupported locale"}), 400
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
                return _notify("Label name is required.", "error")
            if classification_repository.get_label_by_name(db.session, name):
                return _notify(f"Label '{name}' already exists.", "error")
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
            return _notify("Classify-labels automation is not installed.", "error")
        media_item_ids = media_repository.get_indexed_media_item_ids(db.session)
        if not media_item_ids:
            return _notify("No indexed photos to classify yet.", "error")
        classify_labels_automation_task(automation.id, media_item_ids)
        return _notify(f"Re-classifying {len(media_item_ids)} photo(s) in the background…")

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
            "showNotification": {"message": "AI model updated.", "type": "success"}
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
        data = request.get_json()
        new_dir = data.get("directory", "").strip()

        if not new_dir:
            return jsonify({"error": "Directory path is required"}), 400

        try:
            Path(new_dir).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return jsonify({"error": f"Failed to create directory: {str(e)}"}), 400

        if yaffo.db.repositories.media_dir_repository.add_media_dir(db.session, new_dir) is None:
            return jsonify({"error": "Directory already exists"}), 400

        return jsonify({"success": True, "media_dirs": yaffo.db.repositories.media_dir_repository.list_media_dirs(db.session)})

    @app.route("/api/settings/media-dirs/<int:index>", methods=["DELETE"])
    def remove_media_dir(index: int):
        """Remove a media directory by index"""
        removed = yaffo.db.repositories.media_dir_repository.remove_media_dir(db.session, index)
        if removed is None:
            return jsonify({"error": "Invalid index"}), 400
        return jsonify({
            "success": True,
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
            "success": True,
            "directory": str(thumbnail_dir),
            "count": count,
            "size": size,
            "size_formatted": format_size(size)
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
                            size_formatted=format_size(total_size),
                        ))) + "\n"
                    else:
                        yield json.dumps(asdict(ThumbnailStatsProgress(scanned=event))) + "\n"
            except Exception as e:
                yield json.dumps(asdict(ThumbnailStatsError(message=str(e)))) + "\n"

        # no-store: live data, so the browser re-walks every load rather than caching.
        return Response(
            stream_with_context(generate()),
            mimetype="application/x-ndjson",
            headers={"Cache-Control": "no-store"},
        )

    @app.route("/api/settings/thumbnail-dir", methods=["POST"])
    def update_thumbnail_dir():
        """Update thumbnail directory and move files"""
        data = request.get_json()
        new_dir = data.get("directory", "").strip()

        if not new_dir:
            return jsonify({"error": "Directory path is required"}), 400

        new_dir_path = Path(new_dir)

        # Get current thumbnail directory
        thumbnail_setting = db.session.query(ApplicationSettings).filter_by(name="thumbnail_dir").first()

        if thumbnail_setting and thumbnail_setting.value:
            current_dir = Path(thumbnail_setting.value)
        else:
            current_dir = None

        # Check if new directory is the same as current
        if current_dir and new_dir_path.resolve() == current_dir.resolve():
            return jsonify({"error": "New directory is the same as current directory"}), 400

        try:
            # Create new directory if it doesn't exist
            new_dir_path.mkdir(parents=True, exist_ok=True)

            # Get stats before moving
            file_count, total_size = get_thumbnail_stats(current_dir)

            # Track path mappings for database update
            path_mappings = {}

            # Move files
            if current_dir and current_dir.exists() and file_count > 0:
                for file_path in current_dir.rglob("*"):
                    if file_path.is_file():
                        relative_path = file_path.relative_to(current_dir)
                        dest_path = new_dir_path / relative_path
                        dest_path.parent.mkdir(parents=True, exist_ok=True)

                        # Track the mapping of old path to new path
                        path_mappings[str(file_path)] = str(dest_path)

                        shutil.move(str(file_path), str(dest_path))

            # Update Face table with new thumbnail paths
            faces_updated = 0
            if path_mappings:
                faces = db.session.query(Face).filter(Face.full_file_path.in_(path_mappings.keys())).all()
                for face in faces:
                    if face.full_file_path in path_mappings:
                        face.full_file_path = path_mappings[face.full_file_path]
                        faces_updated += 1

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
                "success": True,
                "new_directory": str(new_dir_path),
                "files_moved": file_count,
                "faces_updated": faces_updated,
                "size_moved": format_size(total_size)
            })

        except Exception as e:
            db.session.rollback()
            return jsonify({
                "error": f"Failed to move thumbnails: {str(e)}"
            }), 500
