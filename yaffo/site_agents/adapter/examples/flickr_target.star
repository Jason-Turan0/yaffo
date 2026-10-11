# Flickr sets: keep selected Yaffo photos in Flickr photosets.
#
# A custom adapter (OAuth 1.0a, the user's own Flickr app). Each routing target is a
# set: the set the user picked, or one titled after the target, created when missing.
# Flickr creates a set with its first photo, so a new set's first upload is already in
# it. Uploads are private (hidden from public searches); Flickr answers an upload in
# XML, and everything else in JSON with "stat": "ok" or "fail" (failures are HTTP 200).
#
# Removing takes the photo out of its set and deletes it from Flickr, since Yaffo
# uploaded it.

REST = "https://api.flickr.com/services/rest"
UPLOAD = "https://up.flickr.com/services/upload/"

def _error(r):
    if r["status"] == 0:
        return {"code": "retry_later", "message": r.get("error", "network")}
    if r["status"] == 401 or r["status"] == 403:
        return {"code": "auth", "message": "Flickr refused the account"}
    body = r.get("json")
    if type(body) == "dict" and body.get("stat") == "fail":
        if body.get("code") == 98 or body.get("code") == 99:
            return {"code": "auth", "message": "Flickr: " + body.get("message", "")}
        return {"code": "retry_later", "message": "Flickr: %s (%s)" % (body.get("message", ""), body.get("code"))}
    return {"code": "retry_later", "message": "Flickr answered %d" % r["status"]}

def _ok(r):
    return r["status"] == 200 and type(r.get("json")) == "dict" and r["json"].get("stat") == "ok"

def _fail_code(r):
    body = r.get("json")
    return body.get("code") if type(body) == "dict" else None

def _read(method, params):
    query = {"method": method, "format": "json", "nojsoncallback": "1"}
    query.update(params)
    return web_request(CONNECTION, "GET", REST, query=query)

def _write(method, params):
    query = {"method": method, "format": "json", "nojsoncallback": "1"}
    query.update(params)
    return web_request(CONNECTION, "POST", REST, query=query)

def _upload_error(up):
    text = up.get("text") or ""
    if up["status"] == 200 and ('<err code="98"' in text or '<err code="99"' in text):
        return {"code": "auth", "message": "Flickr refused the account"}
    return _error(up)

def _photo_id(text):
    start = text.find("<photoid>")
    end = text.find("</photoid>")
    if start < 0 or end < start:
        return None
    return text[start + len("<photoid>"):end].strip()

def _set_title(target, ctx):
    pinned = ctx["options"].get("set")
    if pinned and pinned.get("new"):
        return pinned["label"]
    return target

def _existing_sets():
    r = _read("flickr.photosets.getList", {"per_page": "500"})
    if not _ok(r):
        return {"error": _error(r)}
    found = {}
    for photoset in r["json"]["photosets"].get("photoset", []):
        found[photoset["title"]["_content"]] = photoset["id"]
    return {"sets": found}

def push(batch, ctx):
    sets = dict(ctx["state"].get("sets") or {})
    pinned = ctx["options"].get("set")
    results = []
    grouped = {}
    for item in batch:
        grouped.setdefault(_set_title(item["target"], ctx), []).append(item)
    for title, items in grouped.items():
        uploaded = []
        for item in items:
            up = web_upload_media(CONNECTION, "POST", UPLOAD, item["media_item_id"], encoding="multipart",
                                  field="photo",
                                  fields={"title": item["filename"], "description": item["caption"] or "",
                                          "is_public": "0", "is_friend": "0", "is_family": "0", "hidden": "2"})
            photo_id = _photo_id(up.get("text") or "") if up["status"] == 200 else None
            if not photo_id:
                error = _upload_error(up)
                if error["code"] == "auth":
                    return {"results": results, "state_patch": {"sets": sets}, "error": error}
                results.append({"key": item["key"], "error": error})
                continue
            uploaded.append((item, photo_id))
        if not uploaded:
            continue
        set_id = pinned["id"] if pinned and pinned.get("id") else sets.get(title)
        primary = None
        if not set_id:
            existing = _existing_sets()
            if existing.get("error"):
                return {"results": results, "state_patch": {"sets": sets}, "error": existing["error"]}
            set_id = existing["sets"].get(title)
        if not set_id:
            primary = uploaded[0][1]
            created = _write("flickr.photosets.create", {"title": title, "primary_photo_id": primary})
            if not _ok(created):
                return {"results": results, "state_patch": {"sets": sets}, "error": _error(created)}
            set_id = created["json"]["photoset"]["id"]
        sets[title] = set_id
        for item, photo_id in uploaded:
            if photo_id != primary:
                added = _write("flickr.photosets.addPhoto", {"photoset_id": set_id, "photo_id": photo_id})
                if not _ok(added) and _fail_code(added) != 3:      # 3: already in the set
                    results.append({"key": item["key"], "error": _error(added)})
                    continue
            results.append({"key": item["key"], "remote_id": photo_id,
                            "details": {"photoset_id": set_id, "uploaded": True}})
    return {"results": results, "state_patch": {"sets": sets}}

def remove(items, ctx):
    results = []
    for item in items:
        details = item.get("details") or {}
        if item.get("remote_id") and details.get("photoset_id"):
            out = _write("flickr.photosets.removePhoto", {"photoset_id": details["photoset_id"],
                                                          "photo_id": item["remote_id"]})
            if not _ok(out) and _fail_code(out) not in (1, 2):    # 1, 2: the set or photo is gone already
                return {"results": results, "error": _error(out)}
        if item.get("remote_id") and details.get("uploaded"):
            out = _write("flickr.photos.delete", {"photo_id": item["remote_id"]})
            if not _ok(out) and _fail_code(out) != 1:              # 1: already deleted
                return {"results": results, "error": _error(out)}
        results.append({"key": item["key"]})
    return {"results": results}

def list_remote(cursor, ctx):
    targets_of = {}
    for item in ctx.get("reconcile_items") or []:
        set_id = (item.get("details") or {}).get("photoset_id") or (ctx["state"].get("sets") or {}).get(
            _set_title(item["target"], ctx))
        if set_id:
            targets_of.setdefault(set_id, {})[item["target"]] = True
    keys = sorted(targets_of.keys())
    index = int(cursor) if cursor else 0
    if index >= len(keys):
        return {"items": [], "next_cursor": None}
    set_id = keys[index]
    r = _read("flickr.photosets.getPhotos", {"photoset_id": set_id, "per_page": "500"})
    next_cursor = str(index + 1) if index + 1 < len(keys) else None
    if _fail_code(r) == 1:                                        # the set is gone
        return {"items": [], "next_cursor": next_cursor}
    if not _ok(r):
        return {"error": _error(r)}
    found = [{"remote_id": photo["id"], "target": target, "filename": photo.get("title"),
              "details": {"photoset_id": set_id}}
             for photo in r["json"]["photoset"].get("photo", [])
             for target in targets_of[set_id].keys()]
    return {"items": found, "next_cursor": next_cursor}

def choices(option, parent_id, cursor, ctx):
    existing = _existing_sets()
    if existing.get("error"):
        return {"error": existing["error"]}
    return {"items": [{"id": set_id, "label": title, "has_children": False}
                      for title, set_id in existing["sets"].items()],
            "next_cursor": None}

ADAPTER = {
    "contract": 1,
    "role": "target",
    "provider": "custom",
    "title": "Flickr sets",
    "description": "Keep selected photos in Flickr sets, uploaded as private photos.",
    "connection": {
        "auth_kind": "oauth1a",
        "auth": {"request_token_url": "https://www.flickr.com/services/oauth/request_token",
                 "authorize_url": "https://www.flickr.com/services/oauth/authorize",
                 "access_token_url": "https://www.flickr.com/services/oauth/access_token",
                 "authorize_params": {"perms": "delete"},
                 "sign_multipart_fields": True},
        "redirect": "registered",
        "app_credentials": ["client_id", "client_secret"],
        "app_form_labels": {"redirect": "Callback URL", "client_id": "Key", "client_secret": "Secret"},
        "base_urls": ["https://api.flickr.com", "https://up.flickr.com"],
        "check": {"method": "GET",
                  "url": "https://api.flickr.com/services/rest?method=flickr.test.login&format=json&nojsoncallback=1",
                  "account_id": "user.id", "account_label": ["user.username._content"]},
    },
    "options": {"set": {"type": "remote_collection", "label": "Flickr set", "create": True,
                        "help": "Choose a set, or name a new one. Leave empty to name it after the selection."}},
    "capabilities": {"max_batch": 10, "remove": True, "reconcile": True, "targets": "many",
                     "accepts": {"types": ["image/jpeg", "image/png", "image/gif", "image/tiff",
                                           "video/mp4", "video/quicktime"],
                                 "max_bytes": 200000000}},
    "hooks": {"push": push, "remove": remove, "list_remote": list_remote, "choices": choices},
}
