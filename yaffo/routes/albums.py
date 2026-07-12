"""Albums: curated collections of media items (docs/development/p2p-sharing.md,
Phase 7).

Its own top-level tab, laid out like the Automations screen — a sidebar listing
the user's albums beside the selected album's detail. The overview (`/albums`) is
the landing screen: album tiles with covers, which is the surface a cover exists
for.

Sharing an album is a p2p share grant with the `album` scope; this module only
*shows* which devices an album is shared with. Grants are created from the album's
Share action and revoked from the Sharing sidebar, which already owns revocation
for every scope.
"""
from dataclasses import dataclass
from typing import Optional

from flask import Flask, abort, redirect, render_template, request, url_for

from yaffo.db import db
from yaffo.db.models import GRANT_SCOPE_ALBUM, TRUST_STATE_TRUSTED, Album, MediaItem
from yaffo.db.repositories import album_repository as repo
from yaffo.db.repositories import p2p_repository
from yaffo.db.repositories.media_filter_repository import apply_media_filters
from yaffo.routes import filter_config
from yaffo.routes.filter_panel import build_filters_context, to_media_filters, to_query_params
from yaffo.routes.selection import SELECTION_ARGS, Selection, selection_from_args

# Same ladder the home gallery offers.
_PAGE_SIZES = [10, 25, 50, 100, 250]


@dataclass(frozen=True)
class AlbumView:
    """One album as the sidebar, the overview tiles, and the album header need it,
    so the templates stay dumb and the counts are fetched once, not per row."""

    id: int
    name: str
    description: Optional[str]
    item_count: int
    # The cover item itself, not just its id: a video cover must be drawn from its
    # poster, since an <img> cannot render the original file. None when empty.
    cover: Optional[MediaItem]
    cover_media_item_id: Optional[int]  # marks the covering card on the album page
    shared_with: tuple[str, ...]  # display names of devices holding an active grant

    @property
    def is_shared(self) -> bool:
        return bool(self.shared_with)


def init_albums_routes(app: Flask):

    def _shared_device_names() -> dict[int, list[str]]:
        """album id -> the devices it is actively shared with. One pass over the
        active grants; an album with no grant simply has no entry."""
        devices = {
            device.device_id: device.display_name or device.device_id
            for device in p2p_repository.list_known_devices(db.session)
        }
        shared: dict[int, list[str]] = {}
        for grant in p2p_repository.list_active_grants(db.session):
            if grant.scope_type != GRANT_SCOPE_ALBUM or grant.album_id is None:
                continue
            name = devices.get(grant.peer_device_id, grant.peer_device_id)
            shared.setdefault(grant.album_id, []).append(name)
        return shared

    def _album_views() -> list[AlbumView]:
        albums = repo.list_albums(db.session)
        counts = repo.item_counts(db.session)
        shared = _shared_device_names()
        views = []
        for album in albums:
            cover = repo.cover_media_item(db.session, album)
            views.append(
                AlbumView(
                    id=album.id,
                    name=album.name,
                    description=album.description,
                    item_count=counts.get(album.id, 0),
                    cover=cover,
                    cover_media_item_id=cover.id if cover else None,
                    shared_with=tuple(shared.get(album.id, ())),
                )
            )
        return views

    def _shareable_devices(album_id: int) -> list[dict]:
        """Paired devices for the Share modal: the trusted ones, each flagged with
        whether this album is already shared with it (revoking lives in the Sharing
        sidebar, so a device already granted is simply not offered again)."""
        granted = {
            grant.peer_device_id
            for grant in p2p_repository.list_active_grants(db.session)
            if grant.scope_type == GRANT_SCOPE_ALBUM and grant.album_id == album_id
        }
        return [
            {
                "device_id": device.device_id,
                "display_name": device.display_name or device.device_id,
                "shared": device.device_id in granted,
            }
            for device in p2p_repository.list_known_devices(db.session)
            if device.trust_state == TRUST_STATE_TRUSTED
        ]

    def _require_album(album_id: int) -> Album:
        album = repo.get_album(db.session, album_id)
        if album is None:
            abort(404)
        return album

    def _selection(total: int) -> Selection:
        """The selection carried on the querystring (routes/selection.py). Read
        identically by the screen that renders it and by the POST that acts on it."""
        return selection_from_args(request.args, total=total, cast=int)

    @app.route("/albums", methods=["GET"])
    def albums_index():
        """The landing screen: album tiles. Replaces a "nothing selected" empty
        state, and is what gives an album cover somewhere to be seen."""
        return render_template("albums/index.html", albums=_album_views(), selected_id=None)

    @app.route("/albums/<int:album_id>", methods=["GET"])
    def albums_show(album_id: int):
        album = _require_album(album_id)
        views = _album_views()
        selected = next(view for view in views if view.id == album.id)
        items = repo.list_items(db.session, album.id)
        # Edit mode is `?edit=1`, not a JS flag: it survives a reload and the Back
        # button, and the server can render the grid already in selection mode.
        return render_template(
            "albums/album.html",
            albums=views,
            selected=selected,
            selected_id=album.id,
            items=items,
            editing=request.args.get("edit") == "1",
            selection=_selection(total=len(items)),
            devices=_shareable_devices(album.id),
        )

    @app.route("/albums/create", methods=["POST"])
    def albums_create():
        try:
            album = repo.create_album(
                db.session,
                request.form.get("name") or "",
                request.form.get("description"),
            )
        except ValueError as exc:
            return render_template(
                "albums/index.html", albums=_album_views(), selected_id=None, error=str(exc)
            ), 400
        return redirect(url_for("albums_show", album_id=album.id))

    @app.route("/albums/<int:album_id>/details", methods=["POST"])
    def albums_update_details(album_id: int):
        """Rename / re-describe — the "Edit details" modal. Membership is edited on
        the grid, never here."""
        _require_album(album_id)
        try:
            repo.update_album(
                db.session,
                album_id,
                request.form.get("name") or "",
                request.form.get("description"),
            )
        except ValueError as exc:
            return _render_album(album_id, error=str(exc)), 400
        return redirect(url_for("albums_show", album_id=album_id))

    @app.route("/albums/<int:album_id>/add", methods=["GET"])
    def albums_add(album_id: int):
        """The bulk-add screen: the home page's filter panel and grid, with the
        cards selectable. It is a screen of its own rather than a panel on the
        album page because the filter panel *is* a left sidebar, and the album nav
        already occupies that column."""
        album = _require_album(album_id)
        filters = build_filters_context(db.session, request.args)
        page = request.args.get("page", default=1, type=int)
        page_size = request.args.get("page-size", default=25, type=int)

        query = (
            db.session.query(MediaItem)
            .order_by(MediaItem.date_taken.desc())
        )
        query = apply_media_filters(db.session, query, to_media_filters(filters))
        # Photos already in the album are not offered: adding them is a no-op, and
        # showing them makes the "N matching" count a lie about what Add would do.
        query = repo.exclude_members(query, album_id)
        total = query.count()
        media_items = query.limit(page_size).offset((page - 1) * page_size).all()

        filters["page_sizes"] = _PAGE_SIZES
        filters["page_size"] = page_size
        selection = _selection(total=total)
        return render_template(
            "albums/add.html",
            album=album,
            media_items=media_items,
            match_count=total,  # the size of "select all matching these filters"
            album_count=repo.item_count(db.session, album_id),
            added_count=request.args.get("added", type=int),
            selection=selection,
            filters=filters,
            filter_params=to_query_params(filters),
            # Pagination must carry the selection, or paging would drop it.
            page_params={**to_query_params(filters), **selection.query_params},
            filter_layout=filter_config.load_layout(db.session),
            filter_default_keys=filter_config.default_keys(),
            pagination={
                "current_page": page,
                "total_items": total,
                "page_size": page_size,
                "page_sizes": _PAGE_SIZES,
            },
        )

    @app.route("/albums/<int:album_id>/items/add", methods=["POST"])
    def albums_add_items(album_id: int):
        """Add the add screen's selection, and come back to the add screen.

        `scope=all` means every photo matching the filters — which arrive on the
        querystring, exactly as the grid was showing them — minus any `exclude_id`
        the user unticked, so the whole match is added by one INSERT ... SELECT,
        however many photos that is. Otherwise the ticked ids are added.

        Adding returns here rather than to the album because curating is a loop:
        filter, add, filter again. The photos just added drop out of the results
        (they are members now), so the grid shows what is left to add."""
        _require_album(album_id)
        selection = _selection(total=0)  # total is only needed for display
        if selection.all:
            filters = build_filters_context(db.session, request.args)
            added = repo.add_matching(
                db.session,
                album_id,
                to_media_filters(filters),
                exclude_ids=sorted(selection.excluded),
            )
        else:
            added = repo.add_items(db.session, album_id, sorted(selection.ids))
        return redirect(_add_screen_url(album_id, added=added))

    # Not filters: transient screen state that must never be echoed back into the
    # next request. `added` round-trips (the redirect sets it, the add form copies
    # the querystring into its action, so a second add would carry the previous
    # one's value and collide); the selection has just been consumed, so it must not
    # come back either.
    _NON_FILTER_ARGS = {"added", *SELECTION_ARGS}

    def _add_screen_url(album_id: int, added: int) -> str:
        """Back to the add screen with the same filters — a POST must not lose the
        filters the user built up to make the selection."""
        args = {
            key: request.args.getlist(key)
            for key in request.args
            if key not in _NON_FILTER_ARGS
        }
        return url_for("albums_add", album_id=album_id, added=added, **args)

    @app.route("/albums/<int:album_id>/items/remove", methods=["POST"])
    def albums_remove_items(album_id: int):
        """Remove members — the edit mode's "Remove from album".

        `scope=all` means every member of the album (minus any `exclude_id` the user
        unticked), not the ids that happened to be on the rendered page: the
        selection bar posts the scope, so emptying a 500-photo album is one DELETE.
        Nothing here touches a photo or its file."""
        _require_album(album_id)
        selection = _selection(total=0)
        if selection.all:
            repo.remove_all(db.session, album_id, exclude_ids=sorted(selection.excluded))
        else:
            repo.remove_items(db.session, album_id, sorted(selection.ids))
        # Back to the album with the selection dropped: it has been acted on.
        return redirect(url_for("albums_show", album_id=album_id, edit="1"))

    @app.route("/albums/<int:album_id>/cover", methods=["POST"])
    def albums_set_cover(album_id: int):
        """Pin the cover shown on the overview tile."""
        _require_album(album_id)
        media_item_id = request.form.get("media_item_id", type=int)
        if media_item_id is None:
            abort(400)
        try:
            repo.set_cover(db.session, album_id, media_item_id)
        except ValueError as exc:
            return _render_album(album_id, error=str(exc)), 400
        return redirect(url_for("albums_show", album_id=album_id))

    @app.route("/albums/<int:album_id>/reorder", methods=["POST"])
    def albums_reorder(album_id: int):
        """Persist manual order after a drag. Called by fetch (the grid has already
        moved the card), so it answers 204 rather than re-rendering the page."""
        _require_album(album_id)
        repo.reorder(db.session, album_id, _posted_item_ids())
        return "", 204

    def _posted_item_ids() -> list[int]:
        return [
            item_id
            for item_id in request.form.getlist("media_item_id", type=int)
            if item_id is not None
        ]

    @app.route("/albums/<int:album_id>/share", methods=["POST"])
    def albums_share(album_id: int):
        """Reconcile who this album is shared with: the checked devices are exactly
        the devices that end up holding an `album` grant on it.

        Checking a device grants it; UNCHECKING revokes — so the modal shows the
        truth and can undo itself, rather than being a one-way "add" whose only undo
        lives on another screen. (The Sharing sidebar still revokes any grant; this is
        the same operation from the album's side.)

        A grant authorizes the album's membership as it stands AT REQUEST TIME, so
        adding or removing photos later changes what the peer sees on its next pull."""
        _require_album(album_id)
        wanted = set(request.form.getlist("device_id"))
        granted = {
            grant.peer_device_id: grant
            for grant in p2p_repository.list_active_grants(db.session)
            if grant.scope_type == GRANT_SCOPE_ALBUM and grant.album_id == album_id
        }

        for device in p2p_repository.list_known_devices(db.session):
            if device.trust_state != TRUST_STATE_TRUSTED:
                continue  # a revoked device cannot be granted anything
            grant = granted.get(device.device_id)
            if device.device_id in wanted and grant is None:
                p2p_repository.create_grant(
                    db.session, device.device_id, GRANT_SCOPE_ALBUM, album_id=album_id
                )
            elif device.device_id not in wanted and grant is not None:
                p2p_repository.revoke_grant(db.session, grant.id)
        return redirect(url_for("albums_show", album_id=album_id))

    @app.route("/albums/<int:album_id>/delete", methods=["POST"])
    def albums_delete(album_id: int):
        """Delete the album and its membership rows — never the photos."""
        if not repo.delete_album(db.session, album_id):
            abort(404)
        return redirect(url_for("albums_index"))

    def _render_album(album_id: int, error: Optional[str] = None):
        album = _require_album(album_id)
        views = _album_views()
        selected = next(view for view in views if view.id == album.id)
        return render_template(
            "albums/album.html",
            albums=views,
            selected=selected,
            selected_id=album.id,
            items=repo.list_items(db.session, album.id),
            error=error,
        )
