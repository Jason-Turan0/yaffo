# Copy selected photos into child folders of a chosen OneDrive folder.
GRAPH = "https://graph.microsoft.com/v1.0"
CHUNK = 1638400

def _error(r):
    status = r["status"]
    if status == 401 or status == 403:
        return {"code": "auth", "message": "OneDrive refused the account"}
    if status == 429 or status == 503:
        return {"code": "rate_limited", "message": "OneDrive is busy"}
    if status == 404:
        return {"code": "not_found"}
    return {"code": "retry_later", "message": r.get("error") or "OneDrive request failed"}

def _folder(target, folders, ctx):
    if target in folders:
        return {"id": folders[target]}
    root = ctx["options"]["folder"]["id"]
    url = url_template(GRAPH + "/me/drive/items/{id}/children", id=root)
    for _ in range(20):
        listed = web_request(CONNECTION, "GET", url)
        if listed["status"] != 200:
            return {"error": _error(listed)}
        for entry in listed["json"].get("value", []):
            if entry.get("name") == target and "folder" in entry:
                folders[target] = entry["id"]
                return {"id": entry["id"]}
        url = listed["json"].get("@odata.nextLink")
        if not url:
            break
    if url:
        return {"error": {"code": "retry_later", "message": "too many OneDrive folders"}}
    made = web_request(CONNECTION, "POST",
                       url_template(GRAPH + "/me/drive/items/{id}/children", id=root),
                       json={"name": target, "folder": {},
                             "@microsoft.graph.conflictBehavior": "fail"})
    if made["status"] != 201:
        return {"error": _error(made)}
    folders[target] = made["json"]["id"]
    return {"id": folders[target]}

def push(batch, ctx):
    folders = dict(ctx["state"].get("folders") or {})
    results = []
    for item in batch:
        folder = _folder(item["target"], folders, ctx)
        if folder.get("error"):
            return {"results": results, "state_patch": {"folders": folders}, "error": folder["error"]}
        started = web_request(CONNECTION, "POST",
                              url_template(GRAPH + "/me/drive/items/{id}:/{name}:/createUploadSession",
                                           id=folder["id"], name=item["filename"]),
                              json={"item": {"@microsoft.graph.conflictBehavior": "fail",
                                             "name": item["filename"]}})
        if started["status"] != 200:
            results.append({"key": item["key"], "error": _error(started)})
            continue
        upload_url = started["json"]["uploadUrl"]
        completed = None
        for offset in range(0, item["size"], CHUNK):
            length = min(CHUNK, item["size"] - offset)
            sent = web_upload_media(CONNECTION, "PUT", upload_url, item["media_item_id"],
                                    offset=offset, length=length,
                                    headers={"Content-Range": "bytes %d-%d/%d" % (
                                        offset, offset + length - 1, item["size"])})
            if sent["status"] == 200 or sent["status"] == 201:
                completed = sent["json"]
                break
            if sent["status"] != 202:
                results.append({"key": item["key"], "error": _error(sent)})
                break
        if completed and completed.get("id"):
            results.append({"key": item["key"], "remote_id": completed["id"],
                            "details": {"etag": completed.get("eTag")}})
    return {"results": results, "state_patch": {"folders": folders}}

def remove(items, ctx):
    results = []
    for item in items:
        if not item.get("remote_id"):
            results.append({"key": item["key"], "error": {"code": "not_found"}})
            continue
        etag = item.get("details", {}).get("etag")
        if not etag:
            results.append({"key": item["key"], "error": {"code": "changed"}})
            continue
        r = web_request(CONNECTION, "DELETE",
                        url_template(GRAPH + "/me/drive/items/{id}", id=item["remote_id"]),
                        headers={"If-Match": etag})
        if r["status"] == 204 or r["status"] == 404:
            results.append({"key": item["key"]})
        elif r["status"] == 412:
            results.append({"key": item["key"], "error": {"code": "changed"}})
        else:
            return {"results": results, "error": _error(r)}
    return {"results": results}

def list_remote(cursor, ctx):
    folders = dict(ctx["state"].get("folders") or {})
    for item in ctx.get("reconcile_items") or []:
        if item["target"] not in folders:
            folder = _folder(item["target"], folders, ctx)
            if folder.get("error"):
                return {"error": folder["error"]}
    targets = sorted(folders.keys())
    if not targets:
        return {"items": [], "next_cursor": None}
    parts = cursor.split("|", 1) if cursor else ["0", ""]
    index = int(parts[0])
    if index >= len(targets):
        return {"items": [], "next_cursor": None}
    target = targets[index]
    url = parts[1] if parts[1] else url_template(
        GRAPH + "/me/drive/items/{id}/children", id=folders[target])
    r = web_request(CONNECTION, "GET", url)
    if r["status"] == 404:
        folders.pop(target, None)
        next_cursor = str(index + 1) + "|" if index + 1 < len(targets) else None
        return {"items": [], "next_cursor": next_cursor, "state_patch": {"folders": folders}}
    if r["status"] != 200:
        return {"error": _error(r)}
    found = [{"remote_id": i["id"], "target": target, "filename": i["name"],
              "details": {"etag": i.get("eTag")}}
             for i in r["json"].get("value", []) if "file" in i]
    more = r["json"].get("@odata.nextLink")
    next_cursor = (str(index) + "|" + more) if more else (
        str(index + 1) + "|" if index + 1 < len(targets) else None)
    return {"items": found, "next_cursor": next_cursor, "state_patch": {"folders": folders}}

def choices(option, parent_id, cursor, ctx):
    if cursor:
        url = cursor
    elif parent_id:
        url = url_template(GRAPH + "/me/drive/items/{id}/children", id=parent_id)
    else:
        url = GRAPH + "/me/drive/root/children"
    r = web_request(CONNECTION, "GET", url)
    if r["status"] != 200:
        return {"error": _error(r)}
    return {"items": [{"id": i["id"], "label": i["name"],
                       "has_children": i["folder"].get("childCount", 0) > 0}
                      for i in r["json"].get("value", []) if "folder" in i],
            "next_cursor": r["json"].get("@odata.nextLink")}

ADAPTER = {
    "contract": 1,
    "role": "target",
    "provider": "onedrive",
    "title": "OneDrive folder",
    "description": "Keep selected photos in folders under a OneDrive folder.",
    "scopes": ["Files.ReadWrite"],
    "options": {"folder": {"type": "remote_folder", "label": "OneDrive folder", "required": True}},
    "capabilities": {"max_batch": 5, "remove": True, "reconcile": True, "targets": "many"},
    "hooks": {"push": push, "remove": remove, "list_remote": list_remote, "choices": choices},
}
