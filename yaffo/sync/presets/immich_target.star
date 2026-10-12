# Immich target: keep selected Yaffo photos in Immich albums.
#
# Each routing target is an album: the album the user picked, or one named after the
# target (found by name, created when missing). A photo is uploaded once and added to
# its album. Immich deduplicates uploads by checksum, so a photo Immich already has
# (uploaded by its phone app, or by a run that crashed before it recorded the result)
# comes back as a duplicate with its existing id and is simply added to the album.
#
# Removing takes the photo out of the album, but only if Yaffo put it there: a photo
# that was already in the album (the user's own choice) stays. Only a photo Yaffo
# itself uploaded is also moved to Immich's trash (restorable there for 30 days); one
# Immich already had stays in the library.

def _error(r):
    status = r["status"]
    if status == 401 or status == 403:
        return {"code": "auth", "message": "Immich refused the API key"}
    if status == 429 or status == 503:
        return {"code": "rate_limited", "retry_after_s": 60, "message": "Immich is busy"}
    if status == 0:
        return {"code": "retry_later", "message": r.get("error", "network")}
    body = r.get("json") or {}
    detail = body.get("message") if type(body) == "dict" else None
    return {"code": "retry_later", "message": "Immich answered %d: %s" % (status, detail or (r.get("text") or "")[:300])}

def _albums(ctx):
    return dict(ctx["state"].get("albums") or {})

def _name(target, ctx):
    """The album a routing target goes to: the new album the user named, or one named
    after the target."""
    pinned = ctx["options"].get("album")
    if pinned and pinned.get("new"):
        return pinned["label"]
    return target

def _discover(names):
    r = web_request(CONNECTION, "GET", "{base_url}/api/albums")
    if r["status"] != 200:
        return {"error": _error(r)}
    found = {}
    for album in r["json"] or []:
        if album.get("albumName") in names:
            found[album["albumName"]] = album["id"]
    return {"albums": found}

def _album(target, albums, ctx):
    pinned = ctx["options"].get("album")
    if pinned and pinned.get("id"):
        return {"id": pinned["id"]}
    name = _name(target, ctx)
    if name in albums:
        return {"id": albums[name]}
    existing = _discover([name])
    if existing.get("error"):
        return existing
    if name in existing["albums"]:
        albums[name] = existing["albums"][name]
        return {"id": albums[name]}
    r = web_request(CONNECTION, "POST", "{base_url}/api/albums", json={"albumName": name})
    if r["status"] != 200 and r["status"] != 201:
        return {"error": _error(r)}
    albums[name] = r["json"]["id"]
    return {"id": albums[name]}

def _date(item):
    taken = item.get("taken_at") or {}
    when = taken.get("utc") or taken.get("local")
    if not when:
        return "1970-01-01T00:00:00.000Z"
    if len(when) == 19:
        return when + ".000Z"
    return when

def push(batch, ctx):
    albums = _albums(ctx)
    results = []
    grouped = {}
    for item in batch:
        grouped.setdefault(item["target"], []).append(item)
    for target, items in grouped.items():
        album = _album(target, albums, ctx)
        if album.get("error"):
            return {"results": results, "state_patch": {"albums": albums}, "error": album["error"]}
        uploaded = []
        for item in items:
            when = _date(item)
            upload = web_upload_media(CONNECTION, "POST", "{base_url}/api/assets", item["media_item_id"],
                                      encoding="multipart", field="assetData",
                                      fields={"deviceAssetId": "yaffo-" + item["key"], "deviceId": "yaffo",
                                              "fileCreatedAt": when, "fileModifiedAt": when,
                                              "filename": item["filename"]})
            if upload["status"] != 200 and upload["status"] != 201:
                results.append({"key": item["key"], "error": _error(upload)})
                continue
            reply = upload["json"] or {}
            uploaded.append((item, reply["id"], reply.get("status") == "created"))
        if uploaded:
            ids = [entry[1] for entry in uploaded]
            added = web_request(CONNECTION, "PUT", url_template("{base_url}/api/albums/{id}/assets", id=album["id"]),
                                json={"ids": ids})
            if added["status"] != 200:
                return {"results": results, "state_patch": {"albums": albums}, "error": _error(added)}
            refused = {}
            already = {}
            for answer in added["json"] or []:
                if answer.get("success"):
                    continue
                if answer.get("error") == "duplicate":
                    already[answer["id"]] = True       # in the album before Yaffo added it
                else:
                    refused[answer["id"]] = answer.get("error") or "unknown"
            for item, asset_id, created in uploaded:
                if asset_id in refused:
                    results.append({"key": item["key"], "error": {"code": "retry_later",
                                                                  "message": "album refused: " + refused[asset_id]}})
                else:
                    results.append({"key": item["key"], "remote_id": asset_id,
                                    "details": {"album_id": album["id"], "uploaded": created,
                                                "added_to_album": not already.get(asset_id, False)}})
    return {"results": results, "state_patch": {"albums": albums}}

def remove(items, ctx):
    albums = _albums(ctx)
    grouped = {}
    results = []
    for item in items:
        details = item.get("details") or {}
        album_id = details.get("album_id") or albums.get(_name(item["target"], ctx))
        pinned = ctx["options"].get("album")
        if not album_id and pinned:
            album_id = pinned.get("id")
        # Rows from before added_to_album was recorded: Yaffo surely added what it uploaded.
        added = details.get("added_to_album", details.get("uploaded", False))
        if album_id and item.get("remote_id") and added:
            grouped.setdefault(album_id, []).append(item)
        else:
            results.append({"key": item["key"]})       # nothing Yaffo put there to take out
    trash = []
    for album_id, members in grouped.items():
        ids = [member["remote_id"] for member in members]
        r = web_request(CONNECTION, "DELETE", url_template("{base_url}/api/albums/{id}/assets", id=album_id),
                        json={"ids": ids})
        if r["status"] != 200 and r["status"] != 404:
            return {"results": results, "state_patch": {"albums": albums}, "error": _error(r)}
        for member in members:
            results.append({"key": member["key"]})
            if (member.get("details") or {}).get("uploaded"):
                trash.append(member["remote_id"])
    if trash:
        r = web_request(CONNECTION, "DELETE", "{base_url}/api/assets", json={"ids": trash, "force": False})
        if r["status"] != 204 and r["status"] != 200:
            return {"results": [], "state_patch": {"albums": albums}, "error": _error(r)}
    return {"results": results, "state_patch": {"albums": albums}}

def list_remote(cursor, ctx):
    albums = _albums(ctx)
    pinned = ctx["options"].get("album")
    targets_of = {}
    for item in ctx.get("reconcile_items") or []:
        album_id = (item.get("details") or {}).get("album_id")
        if not album_id and pinned and pinned.get("id"):
            album_id = pinned["id"]
        if not album_id:
            album_id = albums.get(_name(item["target"], ctx))
        if album_id:
            targets_of.setdefault(album_id, {})[item["target"]] = True
    keys = sorted(targets_of.keys())
    parts = cursor.split("|", 1) if cursor else ["0", "1"]
    index = int(parts[0])
    if index >= len(keys):
        return {"items": [], "next_cursor": None}
    album_id = keys[index]
    page = int(parts[1] or "1")
    r = web_request(CONNECTION, "POST", "{base_url}/api/search/metadata",
                    json={"page": page, "size": 500, "albumIds": [album_id], "order": "asc"})
    if r["status"] == 404:
        next_cursor = str(index + 1) + "|1" if index + 1 < len(keys) else None
        return {"items": [], "next_cursor": next_cursor}
    if r["status"] != 200:
        return {"error": _error(r)}
    assets = (r["json"] or {}).get("assets") or {}
    found = [{"remote_id": asset["id"], "target": routed, "filename": asset.get("originalFileName"),
              "details": {"album_id": album_id}}
             for asset in assets.get("items", []) if not asset.get("isTrashed")
             for routed in targets_of[album_id].keys()]
    next_page = assets.get("nextPage")
    next_cursor = (str(index) + "|" + next_page) if next_page else (
        str(index + 1) + "|1" if index + 1 < len(keys) else None)
    return {"items": found, "next_cursor": next_cursor}

def choices(option, parent_id, cursor, ctx):
    r = web_request(CONNECTION, "GET", "{base_url}/api/albums")
    if r["status"] != 200:
        return {"error": _error(r)}
    return {"items": [{"id": a["id"], "label": a.get("albumName") or a["id"], "has_children": False}
                      for a in r["json"] or []],
            "next_cursor": None}

ADAPTER = {
    "contract": 1,
    "role": "target",
    "provider": "immich",
    # Immich searches with POST; these only read.
    "reads": ["POST {base_url}/api/search/metadata"],
    "title": "Immich album",
    "description": "Keep selected photos in an Immich album.",
    "options": {"album": {"type": "remote_collection", "label": "Immich album", "create": True,
                          "help": "Choose an album, or name a new one. Leave empty to name it after the selection."}},
    "capabilities": {"max_batch": 20, "remove": True, "reconcile": True, "targets": "many"},
    "hooks": {"push": push, "remove": remove, "list_remote": list_remote, "choices": choices},
}
