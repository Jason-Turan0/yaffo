# Yaffo vs. other open source photo tools

A feature comparison between Yaffo and the alternatives people actually weigh it
against: the self-hosted servers **Immich** and **PhotoPrism**, and the desktop
organizer **digiKam**.

Those three are not one category. Immich is a self-hosted replacement for Google
Photos, built around phone backup for a household. PhotoPrism is a self-hosted
media library and browser for an existing archive. digiKam is a twenty-year-old
desktop application for photographers who want control over every field.

Yaffo is a local-first **organizer**: a desktop tool for working through a
personal collection in batches — reviewing faces, assigning people, clearing
duplicates, applying organization rules — rather than a server everyone in the
house logs into. On *shape*, its nearest neighbor is digiKam, not the servers;
the [head-to-head with digiKam](#desktop-organizers-digikam-and-the-rest) is
further down.

Pick the row that matches how you actually use photos, not the one with the most
checkmarks.

!!! note "As of September 2026"

    Comparisons age quickly. Immich, PhotoPrism, and digiKam all ship
    frequently, and their feature sets below were checked against their own
    documentation and release notes in September 2026. Yaffo's column describes
    what is in this repository today. Verify anything decision-critical against
    the upstream project.

## At a glance

| | **Yaffo** | **Immich** | **PhotoPrism** | **digiKam** |
|---|---|---|---|---|
| **Best at** | Cleaning up a messy personal library | Phone backup + a shared family library | Browsing and searching a large archive | Total control over a photographer's catalog |
| **Shape** | Local desktop app, single user | Client/server, multi-user | Server, multi-user | Local desktop app, single user |
| **License** | MIT | AGPL-3.0 | AGPL-3.0 (Community Edition), paid tiers | GPL-2.0+ |
| **Runs as** | macOS `.app`, `pipx install`, or Docker | Docker Compose (4 containers) | Docker, Kubernetes, NAS, Raspberry Pi | Native Qt app (Linux/Windows/macOS) |
| **Interface** | Browser (responsive) | Browser + native apps | Browser (PWA) | Qt desktop, local machine only |
| **Database** | SQLite | PostgreSQL + pgvector + Valkey/Redis | SQLite or MariaDB | SQLite or MariaDB |
| **Native mobile app** | No (responsive web UI) | **Yes — iOS + Android, auto-backup** | No (PWA; third-party backup apps) | No |
| **Cloud dependency** | None required | None | None | None |

## Deployment and footprint

| | **Yaffo** | **Immich** | **PhotoPrism** |
|---|---|---|---|
| Install path | `pipx install yaffo` + `yaffo setup`; macOS `.app`/DMG; Dockerfile included | Docker Compose stack | Docker / Compose; NAS one-click packages |
| Services to run | One app process + a background task host | Server, machine-learning container, Postgres, Redis-compatible cache | One server container + database |
| Resource footprint | Light — designed for a laptop | Heaviest of the three (vector DB + ML container) | Moderate |
| Accounts / login | **None** — no auth layer, single local user | Multi-user with per-user quotas | Multi-user, roles, guest access, 2FA |
| Runs fully offline | Yes | Yes | Yes |

Yaffo has no authentication because it has no concept of a second user. That is
a deliberate scope choice, and it is also the main reason **not** to expose it
on a network you do not control.

## Library and media support

| | **Yaffo** | **Immich** | **PhotoPrism** |
|---|---|---|---|
| Photos | JPEG, PNG, HEIC | Wide, incl. HEIC | Wide, incl. HEIC |
| **RAW files** | **Not supported** | Yes | Yes |
| Video | MP4, MOV, M4V, WebM inline; AVI/MKV/WMV/FLV cataloged and opened externally | Yes, with HEVC and real-time HLS transcoding | Yes |
| Live Photos | No | Yes | Yes |
| Files stay where they are | **Yes** — Yaffo indexes your configured media directories in place | Optional ("external libraries"); uploads are managed | Yes (originals folder) |
| Originals modified | Off by default; opt in per field and the **`export_photo_tag` automation** writes place names, people, labels, custom tags, favorites, and dates back into the file on every change | No | No |

RAW is the clearest gap. If your library is `.CR2`/`.NEF`/`.ARW` files, Yaffo
will not index them today.

## Finding photos

| | **Yaffo** | **Immich** | **PhotoPrism** |
|---|---|---|---|
| **Ad-hoc** natural-language search ("red car on a beach", typed fresh) | No — needs a vocabulary entry and a re-classification pass | **Yes — CLIP vectors searched at query time** | Partial, via automatic labels |
| Structured filters | **Extensive** — year, month, people (any/all), labels, tags name+value, location, device, favorites, media type, filename/folder | Yes | **Extensive** — labels, location, resolution, color, quality, combinable |
| Proximity / "near this place" search | Yes | Yes | Yes |
| Map view | Yes (OpenStreetMap tiles, no third-party analytics) | Yes | Yes, multiple world map styles |
| Filter sidebar is configurable | Yes — choose which filter groups appear | — | — |

Yaffo and Immich run the **same CLIP model** — Yaffo's encoders are Immich's
pinned ONNX export of `ViT-B-32__openai`. They differ in *when* the text half
runs. Immich stores each image's 512-d vector and embeds your query on the fly,
so any phrase works instantly but nothing is a discrete label: you get a ranked
similarity list that doesn't compose with other filters and can't be corrected.
Yaffo embeds a vocabulary of natural-language prompts up front and stores the
matches as scored labels, so they filter, count, combine with people and dates,
and can be reviewed — at the cost of needing a re-classification pass to search
for something you hadn't thought of.

Yaffo's filters are built for *selecting a working set to act on*, not just for
finding one photo.

## People and faces

| | **Yaffo** | **Immich** | **PhotoPrism** |
|---|---|---|---|
| Face detection + clustering | Yes — InsightFace SCRFD detection, ArcFace embeddings, on ONNX Runtime, fully local | Yes, local ML container | Yes, local |
| Faces in video | Yes — sampled frames | Yes | Yes |
| **Batch face review UI** | **Yes — the core of the app;** assign, merge, and correct groups of faces at a time | Basic | Basic |
| Automatic assignment of new faces to known people | Yes, as an automation | Yes | Yes |
| Correcting a wrong label | First-class workflow | Supported | Supported |

This is the row Yaffo exists for. The motivating complaint behind the project
was hosted services that guess wrong about who is in a photo and give you no
practical way to fix it.

## Organization and metadata

| | **Yaffo** | **Immich** | **PhotoPrism** | **digiKam** |
|---|---|---|---|---|
| Albums | Yes | Yes | Yes | Yes |
| Favorites | Yes | Yes | Yes | Yes (ratings + flags) |
| Custom tags (name + value) | **Yes** | Limited | Labels / keywords | **Hierarchical tags, written to file** |
| **Natural-language** content labels | **Yes — offline CLIP zero-shot against prompts you write** ("a photo of my dog in snow"), stored as scored, filterable labels | Not as labels; the same model powers query-time search instead | Yes (TensorFlow classification, fixed vocabulary) | Yes (YOLOv11 / EfficientNet, fixed classes) |
| Duplicate detection | Yes — perceptual hashing, with a review workflow | Yes | Yes | Yes (fuzzy/similarity search) |
| EXIF viewer / editor | Yes | Yes | Yes | **Best in class** — XMP/IPTC/EXIF + sidecars |
| Geocoding + reverse geocoding | Yes | Yes | Yes (unlimited on paid tiers) | Yes — writes address parts as tags |
| **Place names you author** | **Yes — free text; geocoder only suggests** | No — GeoNames City/State/Country | Derived from its geocoder | Via generic tags |
| Bulk-assign a place by map selection | **Yes — click clusters or shift-drag a box** | Third-party addons only | Batch Edit, not map-driven | Select list → Apply Reverse Geocoding |
| **Geotagging from neighboring photos in time** | **Yes** | No | No | GPX track file required |
| Stacks / archive | No | Partial | Yes | Yes (versioning + grouping) |

### Places are authored, not derived

The location rows deserve unpacking, because they are the clearest case of a
different *model* rather than a different feature count.

The other three treat a place as something **derived from coordinates**: run
reverse geocoding, get administrative geography back — city, state, country.
Immich resolves City/State/Country from a bundled GeoNames database during EXIF
extraction; digiKam's Geolocation Editor writes the address components into the
file as hierarchical tags; PhotoPrism resolves through its own geocoding
service.

Yaffo treats a place as something **you author**. `location_name` is free text
you assign, and reverse geocoding is demoted to a suggestion you approve or
overrule. This matters because "Grandma's house," "the cabin," and "Mom's old
apartment" are not administrative regions — no geocoder will ever return them,
because they exist only in your family's vocabulary. They are also the names you
would actually search for.

Two supporting pieces have no equivalent elsewhere:

- **Neighbor propagation.** Before falling back to the geocoder, Yaffo checks
  whether photos within the configured radius already carry exactly one saved
  name, and suggests that. Naming is a learning loop over your own vocabulary —
  the tenth visit to Grandma's is labeled from the first nine.
- **Bulk assignment from the map.** Click a cluster, shift-click several, or
  shift-drag a box around a region, then assign one name to everything selected.
  In Immich this workflow only exists through third-party addons
  (`immich-places`, Immich Power Tools); digiKam bulk-applies reverse geocoding
  to a list selection, but not an authored name from a map selection.

Authored does not mean trapped in the database. Enabling the `export_photo_tag`
automation writes the name you chose into **`XMP:Location`** — the standard
field for a human-readable place — and it stays in sync as an event-driven
automation, not a manual export you have to remember. digiKam writes the
*decomposed* address (country, state, city) as hierarchical tags, which is
richer for administrative geography and a poor fit for "Grandma's house." The
two are complementary: Yaffo fills the field digiKam leaves empty.

The same authored-over-derived instinct drives the **label vocabulary** (you
decide the categories, rather than accepting whatever a model's training set
produced) and **time-correlation geotagging**, which fills in GPS for a camera
that has none by borrowing coordinates from phone photos taken minutes away.
digiKam's closest tool, the GPS Correlator, needs a `.gpx` track from a GPS
logger you remembered to carry; Yaffo uses photos you already took.

## Sharing

| | **Yaffo** | **Immich** | **PhotoPrism** |
|---|---|---|---|
| Model | **Direct device-to-device P2P** | Server accounts | Server accounts |
| Shared albums between users | Via a share grant to a paired device | Yes | Yes |
| Public web links | No | Yes | Yes (guest sharing) |
| Partner / household sharing | No | Yes | Via user roles |
| Photos copied to a server | **Never** — encrypted peer-to-peer transfer; the relay hub forwards ciphertext and cannot read anything | Yes, to your server | Yes, to your server |
| Trust model | Keypair identity + one-time human pairing code (TOFU, like SSH host keys) | Accounts and passwords | Accounts, roles, 2FA |
| Works on LAN with no internet | Yes (mDNS discovery) | Yes | Yes |

Yaffo's sharing is a different shape entirely: two devices *you* own, or a
device belonging to someone you paired with by hand, pull directly from each
other. There is no account, no cloud copy, and pairing grants access to nothing
until you issue a specific grant over a media dir, folder, or album. The
tradeoff is that there is nothing to send to a relative who does not run Yaffo —
no public link.

## Automation and customization

| | **Yaffo** | **Immich** | **PhotoPrism** |
|---|---|---|---|
| Scheduled + event-driven automations | **Yes** — system-built and AI-generated rules that run on a schedule or on library events | Workflow automation (previewed in v3.0) | Scheduled indexing |
| **AI page builder** | **Yes** — describe a page ("a polaroid wall of our Maine trip"), get a custom, sandboxed widget page over your own photos | No | No |
| Themes | 6 built in, plus AI-generated custom themes | Light/dark | Light/dark |
| Interface languages | 7 (English, Arabic, German, Spanish, French, Hindi, Chinese) | Many (community translated) | Many (community translated) |
| Requires an AI API key | **Only** for the page builder, AI-generated automations, and theme generation — never for face recognition or labeling, which are fully offline | No | No |

The page builder is Yaffo's most unusual feature and has no counterpart in
either project. The model writes only presentation — the HTML, CSS, and JS of a
widget. Every piece of photo data comes from a declarative query the server
validates and runs itself, so a generated widget never touches the database and
has no network channel.

## Desktop organizers: digiKam and the rest

Immich and PhotoPrism are servers. Yaffo is not, so the more honest structural
comparison is against desktop organizers — and in open source that means
**digiKam**.

digiKam is the most capable open source photo manager that exists, and it
overlaps Yaffo more than either server does: local, single-user, SQLite-backed,
with face recognition, duplicate detection, geotagging, hierarchical tags, and
batch tools. It has also been improving quickly. Version 8.6 rewrote face
management around cross-validating KNN and SVM classifiers for a 25–50% speedup,
and 8.3 added deep-learning auto-tagging, now running YOLOv11 and EfficientNet
B7 with a tunable confidence threshold.

So the question is not whether digiKam is good. It is where the two differ.

| | **Yaffo** | **digiKam** |
|---|---|---|
| Interface | Browser-based, responsive — reachable from a phone or another machine on the LAN | Qt desktop, on the one machine it is installed on |
| Time to first useful result | Point it at a folder; indexing, faces, and labels run on their own | Deep configuration tree; setup is a project in itself |
| Face workflow | Batch review built as the primary screen | Powerful, but spread across sidebar, tags, and maintenance dialogs |
| **Label vocabulary** | **Open** — CLIP zero-shot against natural-language prompts you write | **Fixed** — YOLOv11/EfficientNet class lists (COCO, ImageNet) |
| RAW files | No | **Yes, extensive** |
| Photo editing | None — organizing only | Full editor, batch queue manager, light table, tethered shooting |
| Metadata in file | Opt-in, then automatic: `XMP:Location`, `XMP:PersonInImage`, keywords, dates (EXIF fallback without exiftool) | **Comprehensive** — XMP/IPTC/EXIF, sidecars, round-trips with other tools |
| Format coverage | JPEG, PNG, HEIC + common video | Effectively everything |
| Sharing | P2P to a paired device | Export plugins to web services |
| AI page builder / automations | Yes | No |
| Maturity | Young, small | 20 years, large contributor base |

### The vocabulary difference is the real one

Most of the rows above are "digiKam has more of it." One is a genuine
difference in kind.

digiKam's auto-tagging uses *fixed-class* models. YOLOv11 and EfficientNet were
trained to recognize a closed list — roughly 80 COCO categories, or ImageNet's
1000. digiKam can reliably tell you a photo contains a dog. It cannot be asked
for "my dog asleep in the snow" or "kids at a birthday party," because those are
not classes anyone trained into the model.

Yaffo's CLIP zero-shot labeling has no class list. You write the prompt, and the
model scores every photo against it. Adding a category is typing a sentence, not
retraining anything. That is the capability digiKam's larger feature list does
not contain.

### Why this project exists

The honest origin story: the author spent two hours trying to get digiKam
working and gave up. That is not a knock on digiKam's engineering — it is what
optimizing for the professional power user costs. Every field is exposed,
nothing is assumed, and the first hour goes into configuration rather than
photos.

Yaffo takes the opposite default. Point it at a folder and it starts working:
indexing, face detection, labeling, and geotagging run as automations without
being asked. The design target is that the human only makes the judgment calls —
*is this the same person?* — and the software does the mechanical sorting. If
your reaction to digiKam was "I just wanted to find pictures of my kids," that
gap is the entire reason this exists.

### The wider desktop field

| Tool | What it is | Why it is not in the table |
|---|---|---|
| **Shotwell** (GNOME), **gThumb**, **Gwenview** (KDE) | Lightweight browsers with tagging and basic editing | No face recognition, no ML labeling |
| **darktable**, **RawTherapee** | RAW developers — Lightroom competitors | Editing tools with a library attached, not organizers |
| **Lightroom Classic**, **ACDSee**, **Photo Mechanic** | Commercial desktop DAM + editing | Closed source, subscription or paid license |
| **Excire Foto** | Local AI search with face recognition and semantic keywords | Closed source; the closest commercial analogue to Yaffo's labeling |
| **Mylio Photos** | Local-first sync across devices with no cloud | Closed source; conceptually near Yaffo's P2P model |
| **Apple Photos** | On-device face recognition, already installed | Closed, macOS/iOS only, limited correction of its guesses |
| **Picasa** | Fast local organizer with good face recognition | **Discontinued in 2016** — and still the thing people say they miss |

## Choosing

**Choose Immich if** you want to stop paying Google Photos: phones backing up
automatically, several people in a household with their own logins, links you
can send to relatives, and RAW files. It is the most complete product of the
three, and the heaviest to run.

**Choose PhotoPrism if** you have a large existing archive on a NAS and mainly
want to browse and search it, with RAW and Live Photo support and strong search
filters. Check the edition tiers first — some features (advanced maps,
unlimited geocoding, the admin UI) are behind paid plans.

**Choose digiKam if** you are a photographer who wants every field under your
control: RAW development, meticulous XMP/IPTC metadata that travels with the
files, a batch queue, and the deepest feature set in open source photo
management. Budget an afternoon for setup.

**Choose Yaffo if** your problem is that your library is a *mess*: thousands of
photos with the wrong people attached, duplicates everywhere, missing GPS, and
no labels you actually chose. Yaffo is built for the cleanup work, in batches,
on your own machine, with no account and nothing uploaded. It is also the right
choice if you specifically do not want a server — or if you want to share with
another device without a cloud copy in between.

**Run more than one.** All four index files in place without rewriting
originals, so pointing Yaffo at a folder that Immich, PhotoPrism, or digiKam
also reads is a reasonable setup: organize and clean up in Yaffo, then browse,
back up, or develop RAW elsewhere. Yaffo's `export_photo_tag` automation exists
partly for this — the people, places, and labels you assign are written into the
files as standard XMP, so other tools see your work without a migration.

## Honest gaps in Yaffo

Stated plainly, because a comparison that only flatters its author is not useful:

- **No RAW support.**
- **No native mobile app and no phone auto-backup.** The web UI is responsive,
  but nothing pulls photos off your phone for you.
- **No multi-user accounts and no authentication.** It is a single-user desktop
  app, not a server to expose.
- **No ad-hoc semantic search.** Labels are natural language, but the
  vocabulary is declared up front; you cannot type an unanticipated phrase and
  search on it without re-classifying.
- **No public share links.**
- **Much smaller project.** Immich and PhotoPrism have large contributor bases,
  years of hardening, and packaged NAS installs. Yaffo does not.

## Sources

Competitor details were checked against the projects' own documentation in
September 2026:

- [Immich documentation](https://docs.immich.app/) and
  [immich.app](https://immich.app/)
- [PhotoPrism documentation](https://docs.photoprism.app/) and
  [photoprism.app/features](https://www.photoprism.app/features/)
- [digiKam documentation](https://docs.digikam.org/) and the
  [8.6.0](https://www.digikam.org/news/2025-03-15-8.6.0_release_announcement/)
  and [8.7.0](https://www.digikam.org/news/2025-06-30-8.7.0_release_announcement/)
  release announcements
