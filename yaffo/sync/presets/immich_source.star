# Immich source: import new photos and videos from an Immich server, from one album
# or the whole library.
#
# Immich is self-hosted, so every URL starts with {base_url}, the server the user
# signed in to; Yaffo fills it in and sends the account's API key. The listing is
# Immich's metadata search, oldest first, 200 at a time, filtered to the album when
# one is chosen. Each pass lists everything again; the ledger skips what's unchanged.
#
# Immich's checksum is the file's SHA-1 in base64: as hex it's the record's hash, so a
# photo the library already has is linked without downloading it. Trashed assets
# aren't listed, and Yaffo keeps its copies of anything removed in Immich.

LIBRARY = "library"
PAGE_SIZE = 200

def _error(r):
    status = r["status"]
    if status == 401 or status == 403:
        return {"code": "auth", "message": "Immich refused the API key"}
    if status == 404:
        return {"code": "not_found", "message": "Immich answered 404"}
    if status == 429 or status == 503:
        return {"code": "rate_limited", "retry_after_s": 60, "message": "Immich is busy"}
    if status == 0:
        return {"code": "retry_later", "message": r.get("error", "network")}
    return {"code": "retry_later", "message": "Immich answered %d" % status}

def to_record(asset):
    record = {
        "source_id": asset["id"],
        "filename": asset.get("originalFileName") or asset["id"],
        "folder": "",
        "download": {"url": url_template("{base_url}/api/assets/{id}/original", id=asset["id"])},
        "mime": asset.get("originalMimeType"),
        "updated_at": asset.get("checksum") or asset.get("updatedAt"),
    }
    exif = asset.get("exifInfo") or {}
    if exif.get("fileSizeInByte"):
        record["size"] = exif["fileSizeInByte"]
    sha1 = hex_from_base64(asset.get("checksum"))
    if sha1:
        record["hash"] = {"algo": "sha1", "value": sha1}
    if asset.get("fileCreatedAt"):
        record["taken_at"] = {"utc": asset["fileCreatedAt"]}
    if exif.get("latitude") != None and exif.get("longitude") != None:
        record["location"] = {"lat": exif["latitude"], "lon": exif["longitude"]}
    return record

def list_page(cursor, ctx):
    page = int(cursor) if cursor else 1
    body = {"page": page, "size": PAGE_SIZE, "withExif": True, "order": "asc"}
    album = ctx["options"]["album"]["id"]
    if album != LIBRARY:
        body["albumIds"] = [album]
    r = web_request(CONNECTION, "POST", "{base_url}/api/search/metadata", json=body)
    if r["status"] != 200:
        return {"error": _error(r)}
    assets = (r["json"] or {}).get("assets") or {}
    records = [to_record(a) for a in assets.get("items", []) if not a.get("isTrashed")]
    return {"records": records, "next_cursor": assets.get("nextPage")}

def choices(option, parent_id, cursor, ctx):
    r = web_request(CONNECTION, "GET", "{base_url}/api/albums")
    if r["status"] != 200:
        return {"error": _error(r)}
    items = [{"id": LIBRARY, "label": "Everything in the library", "has_children": False, "suggested": True}]
    for album in r["json"] or []:
        items.append({"id": album["id"], "label": album.get("albumName") or album["id"], "has_children": False})
    return {"items": items, "next_cursor": None}

ADAPTER = {
    "contract": 1,
    "role": "source",
    "provider": "immich",
    # Immich searches with POST; these only read.
    "reads": ["POST {base_url}/api/search/metadata"],
    "title": "Immich album or library",
    "description": "New photos and videos in an Immich album, or in the whole library.",
    "options": {
        "album": {"type": "remote_collection", "label": "Immich album", "required": True,
                  "help": "New photos and videos in this album (or the whole library) are copied into your library. Nothing in Immich is changed."},
    },
    "hooks": {"list_page": list_page, "choices": choices},
}
