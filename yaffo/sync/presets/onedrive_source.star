# OneDrive source: import new photos and videos from a OneDrive folder.
#
# Lists the folder (and its subfolders) with Microsoft Graph's delta feed. The first
# pass lists everything; each later pass starts from the deltaLink the last one
# finished with, so it only sees what changed. Deleted items come back as
# tombstones; Yaffo keeps its copies (see "Source deletions").
#
# Each record names its folder relative to the chosen one ("2024/July"), for the
# keep-the-source's-folders layout. Delta responses don't carry paths, only the
# parent folder's id, so a folder's path is looked up once and kept in state
# ("folders": id -> relative path, "root_path": the chosen folder's own path).

GRAPH = "https://graph.microsoft.com/v1.0"
SELECT = "id,name,size,file,photo,video,image,location,deleted,lastModifiedDateTime,folder,parentReference"
MAX_CACHED_FOLDERS = 400

def _error(r):
    if r["status"] == 401 or r["status"] == 403:
        return {"code": "auth", "message": "graph %d" % r["status"]}
    if r["status"] == 404:
        return {"code": "not_found", "message": "graph 404"}
    if r["status"] == 410:
        return {"code": "cursor_invalid", "message": "graph 410 resyncRequired"}
    if r["status"] == 429 or r["status"] == 503:
        headers = r.get("headers") or {}
        return {"code": "rate_limited", "retry_after_s": int(headers.get("retry-after", "60")),
                "message": "graph %d" % r["status"]}
    if r["status"] == 0:
        return {"code": "retry_later", "message": r.get("error", "network")}
    return {"code": "retry_later", "message": "graph %d" % r["status"]}

def _is_media(i):
    if "photo" in i or "video" in i or "image" in i:
        return True
    mime = i.get("file", {}).get("mimeType", "")
    return mime.startswith("image/") or mime.startswith("video/")

def _item_path(item):
    """An item's full path from a (non-delta) GET of it: its parent's path + its name."""
    parent = item.get("parentReference", {}).get("path")
    if not parent:
        return "/drive/root:"                          # the drive's root itself
    return parent + "/" + item["name"]


def _relative(path, root_path):
    if path == root_path:
        return ""
    if path.startswith(root_path + "/"):
        return path[len(root_path) + 1:]
    return ""


def _folder_of(parent_id, folders, root_path):
    """The relative path of folder `parent_id`, from the cache or one lookup. None if
    the lookup fails (the record is then placed at the top)."""
    if parent_id in folders:
        return folders[parent_id]
    r = web_request(CONNECTION, "GET",
                    url_template(GRAPH + "/me/drive/items/{id}?$select=id,name,parentReference", id=parent_id))
    if r["status"] != 200:
        return None
    relative = _relative(_item_path(r["json"]), root_path)
    folders[parent_id] = relative
    return relative


def to_record(i, folder):
    if "deleted" in i:
        return {"source_id": i["id"], "filename": i.get("name", ""), "deleted": True}
    record = {
        "source_id": i["id"],
        "filename": i["name"],
        "folder": folder or "",
        "download": {"url": url_template(GRAPH + "/me/drive/items/{id}/content", id=i["id"])},
        "size": i.get("size"),
        "mime": i["file"].get("mimeType"),
        "updated_at": i.get("lastModifiedDateTime"),
    }
    sha1 = i["file"].get("hashes", {}).get("sha1Hash")
    if sha1:
        record["hash"] = {"algo": "sha1", "value": sha1.lower()}
    taken = i.get("photo", {}).get("takenDateTime")
    if taken:
        record["taken_at"] = {"utc": taken}
    location = i.get("location")
    if location and "latitude" in location and "longitude" in location:
        record["location"] = {"lat": location["latitude"], "lon": location["longitude"]}
    return record

def list_page(cursor, ctx):
    if cursor:
        url = cursor                                   # a nextLink Graph returned
    elif ctx["state"].get("checkpoint"):
        url = ctx["state"]["checkpoint"]               # the deltaLink of the last full pass
    else:
        url = url_template(GRAPH + "/me/drive/items/{id}/delta?$select=" + SELECT,
                           id=ctx["options"]["folder"]["id"])
    folder_id = ctx["options"]["folder"]["id"]
    root_path = ctx["state"].get("root_path")
    folders = dict(ctx["state"].get("folders") or {})
    if not root_path or ctx["state"].get("root_id") != folder_id:
        top = web_request(CONNECTION, "GET",
                          url_template(GRAPH + "/me/drive/items/{id}?$select=id,name,parentReference", id=folder_id))
        if top["status"] != 200:
            return {"error": _error(top)}
        root_path = _item_path(top["json"])
        folders = {}
    folders[folder_id] = ""
    r = web_request(CONNECTION, "GET", url)
    if r["status"] != 200:
        return {"error": _error(r)}
    body = r["json"]
    records = []
    for i in body.get("value", []):
        parent_id = i.get("parentReference", {}).get("id")
        if "folder" in i and "deleted" not in i and i["id"] != folder_id and parent_id in folders:
            # Folders arrive before their contents: remember their paths for free.
            folders[i["id"]] = (folders[parent_id] + "/" + i["name"]) if folders[parent_id] else i["name"]
        if "deleted" in i:
            records.append(to_record(i, None))
        elif "file" in i and _is_media(i):
            records.append(to_record(i, _folder_of(parent_id, folders, root_path) if parent_id else None))
    if len(folders) > MAX_CACHED_FOLDERS:
        folders = {folder_id: ""}
    return {
        "records": records,
        "next_cursor": body.get("@odata.nextLink"),
        "checkpoint": body.get("@odata.deltaLink"),
        "state_patch": {"root_path": root_path, "root_id": folder_id, "folders": folders},
    }

def choices(option, parent_id, cursor, ctx):
    items = []
    if cursor:
        url = cursor
    elif parent_id:
        url = url_template(GRAPH + "/me/drive/items/{id}/children", id=parent_id)
    else:
        url = GRAPH + "/me/drive/root/children"
        roll = web_request(CONNECTION, "GET", GRAPH + "/me/drive/special/cameraroll")
        if roll["status"] == 200:
            items.append({"id": roll["json"]["id"], "label": roll["json"].get("name", "Camera Roll"),
                          "has_children": True, "suggested": True})
    r = web_request(CONNECTION, "GET", url)
    if r["status"] != 200:
        return {"error": _error(r)}
    known = [item["id"] for item in items]
    for i in r["json"].get("value", []):
        if "folder" in i and i["id"] not in known:
            items.append({"id": i["id"], "label": i["name"],
                          "has_children": i["folder"].get("childCount", 0) > 0})
    return {"items": items, "next_cursor": r["json"].get("@odata.nextLink")}

ADAPTER = {
    "contract": 1,
    "role": "source",
    "provider": "onedrive",
    "title": "OneDrive folder",
    "description": "New photos and videos in a OneDrive folder and its subfolders.",
    "scopes": ["Files.Read"],
    "options": {
        "folder": {"type": "remote_folder", "label": "OneDrive folder", "required": True,
                   "help": "New photos and videos in this folder and its subfolders are copied into your library. Nothing in OneDrive is changed."},
    },
    "hooks": {"list_page": list_page, "choices": choices},
}
