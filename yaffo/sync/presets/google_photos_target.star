# Send selected Yaffo photos to app-created Google Photos albums.
API = "https://photoslibrary.googleapis.com/v1"

def _error(r):
    status = r["status"]
    if status == 401 or status == 403:
        return {"code": "auth", "message": "Google Photos refused the account"}
    if status == 429 or status == 503:
        return {"code": "rate_limited", "message": "Google Photos is busy"}
    return {"code": "retry_later", "message": r.get("error") or "Google Photos request failed"}

def _albums(ctx):
    return dict(ctx["state"].get("albums") or {})

def _discover(targets):
    found = {}
    token = None
    for _ in range(20):
        query = {"pageSize": 50}
        if token:
            query["pageToken"] = token
        page = web_request(CONNECTION, "GET", API + "/albums", query=query)
        if page["status"] != 200:
            return {"error": _error(page)}
        for album in page["json"].get("albums", []):
            if album.get("title") in targets:
                found[album["title"]] = album["id"]
        token = page["json"].get("nextPageToken")
        if not token:
            return {"albums": found}
    return {"error": {"code": "retry_later", "message": "too many Google Photos albums"}}

def _album(target, albums, ctx):
    pinned = ctx["options"].get("album")
    if pinned and pinned.get("id"):
        albums[target] = pinned["id"]
        return {"id": pinned["id"]}
    if target in albums:
        return {"id": albums[target]}
    existing = _discover([target])
    if existing.get("error"):
        return existing
    if target in existing["albums"]:
        albums[target] = existing["albums"][target]
        return {"id": albums[target]}
    r = web_request(CONNECTION, "POST", API + "/albums", json={"album": {"title": target}})
    if r["status"] != 200:
        return {"error": _error(r)}
    albums[target] = r["json"]["id"]
    return {"id": albums[target]}

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
        new_items = []
        pending = []
        for item in items:
            upload = web_upload_media(CONNECTION, "POST", API + "/uploads", item["media_item_id"],
                                      headers={"X-Goog-Upload-Protocol": "raw",
                                               "X-Goog-Upload-Content-Type": item["mime"]})
            if upload["status"] != 200:
                results.append({"key": item["key"], "error": _error(upload)})
                continue
            new_items.append({"description": item["caption"] or "",
                              "simpleMediaItem": {"uploadToken": upload["text"],
                                                  "fileName": item["filename"]}})
            pending.append(item)
        if new_items:
            created = web_request(CONNECTION, "POST", API + "/mediaItems:batchCreate",
                                  json={"albumId": album["id"], "newMediaItems": new_items})
            if created["status"] != 200:
                return {"results": results, "state_patch": {"albums": albums}, "error": _error(created)}
            replies = created["json"].get("newMediaItemResults") or []
            for index, item in enumerate(pending):
                reply = replies[index] if index < len(replies) else {}
                media = reply.get("mediaItem")
                if media and media.get("id"):
                    results.append({"key": item["key"], "remote_id": media["id"]})
                else:
                    results.append({"key": item["key"], "error": {"code": "retry_later"}})
    return {"results": results, "state_patch": {"albums": albums}}

def remove(items, ctx):
    albums = _albums(ctx)
    grouped = {}
    results = []
    for item in items:
        album_id = albums.get(item["target"])
        if album_id:
            grouped.setdefault(album_id, []).append(item)
        else:
            results.append({"key": item["key"], "error": {"code": "not_found"}})
    for album_id, members in grouped.items():
        ids = [member["remote_id"] for member in members if member.get("remote_id")]
        if not ids:
            continue
        r = web_request(CONNECTION, "POST",
                        url_template(API + "/albums/{id}:batchRemoveMediaItems", id=album_id),
                        json={"mediaItemIds": ids})
        if r["status"] == 200 or r["status"] == 204 or r["status"] == 404:
            for member in members:
                results.append({"key": member["key"]})
        else:
            return {"results": results, "error": _error(r)}
    return {"results": results}

def list_remote(cursor, ctx):
    albums = _albums(ctx)
    pinned = ctx["options"].get("album")
    if pinned and pinned.get("id"):
        for item in ctx.get("reconcile_items") or []:
            albums[item["target"]] = pinned["id"]
    missing = [item["target"] for item in ctx.get("reconcile_items") or []
               if item["target"] not in albums]
    if missing:
        discovered = _discover(missing)
        if discovered.get("error"):
            return discovered
        albums.update(discovered["albums"])
    keys = sorted(albums.keys())
    if not keys:
        return {"items": [], "next_cursor": None}
    parts = cursor.split("|", 1) if cursor else ["0", ""]
    index = int(parts[0])
    if index >= len(keys):
        return {"items": [], "next_cursor": None}
    target = keys[index]
    query = {"albumId": albums[target], "pageSize": 100}
    if parts[1]:
        query["pageToken"] = parts[1]
    r = web_request(CONNECTION, "POST", API + "/mediaItems:search", json=query)
    if r["status"] == 404:
        albums.pop(target, None)
        next_cursor = str(index + 1) + "|" if index + 1 < len(keys) else None
        return {"items": [], "next_cursor": next_cursor, "state_patch": {"albums": albums}}
    if r["status"] != 200:
        return {"error": _error(r)}
    found = [{"remote_id": item["id"], "target": target, "filename": item.get("filename")}
             for item in r["json"].get("mediaItems", [])]
    next_page = r["json"].get("nextPageToken")
    next_cursor = (str(index) + "|" + next_page) if next_page else (
        str(index + 1) + "|" if index + 1 < len(keys) else None)
    return {"items": found, "next_cursor": next_cursor, "state_patch": {"albums": albums}}

def choices(option, parent_id, cursor, ctx):
    query = {"pageSize": 50}
    if cursor:
        query["pageToken"] = cursor
    r = web_request(CONNECTION, "GET", API + "/albums", query=query)
    if r["status"] != 200:
        return {"error": _error(r)}
    return {"items": [{"id": a["id"], "label": a["title"], "has_children": False}
                      for a in r["json"].get("albums", [])],
            "next_cursor": r["json"].get("nextPageToken")}

ADAPTER = {
    "contract": 1,
    "role": "target",
    "provider": "google_photos",
    "title": "Google Photos album",
    "description": "Keep selected photos in a Yaffo-created Google Photos album.",
    "scopes": ["https://www.googleapis.com/auth/photoslibrary.appendonly",
               "https://www.googleapis.com/auth/photoslibrary.readonly.appcreateddata",
               "https://www.googleapis.com/auth/photoslibrary.edit.appcreateddata"],
    "options": {"album": {"type": "remote_collection", "label": "Google Photos album",
                          "help": "Only albums created by Yaffo can be used. Leave empty to make one for the selection."}},
    "capabilities": {"max_batch": 50, "remove": True, "reconcile": True, "targets": "many",
                     "accepts": {"types": ["image/avif", "image/bmp", "image/gif", "image/heic",
                                           "image/heif", "image/jpeg", "image/png", "image/tiff",
                                           "image/webp", "video/mp4", "video/quicktime", "video/x-msvideo",
                                           "video/x-matroska", "video/mpeg", "video/3gpp"],
                                 "max_bytes": 200000000}},
    "hooks": {"push": push, "remove": remove, "list_remote": list_remote, "choices": choices},
}
