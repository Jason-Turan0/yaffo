# Distribution & Packaging — Plan / Decision Record

> **Status: PLAN, not user-facing docs.** This captures the decisions and trade-offs
> for open-sourcing and distributing yaffo across macOS, Windows, and Linux. None of
> the channels below are wired up yet except the existing macOS PyInstaller build
> (`packaging/build_dmg.sh`). Treat this as the reference to implement against, not as
> install instructions to publish.

## TL;DR — the matrix

| Platform | Frictionless path | Direct download | Self-contained? |
|----------|-------------------|-----------------|-----------------|
| macOS    | Homebrew cask (own tap) | DMG (signed + notarized) | yes (bundles Python) |
| Windows  | winget / Scoop | installer (signed) | yes |
| Linux    | `pipx` / Flathub | AppImage | AppImage: yes; pip: no |
| All      | `pip install` from PyPI | — | **no** (needs Python + exiftool) |

Two distribution philosophies, pick per audience:
- **PyPI / pipx** — one cross-platform channel, developer audience, *not* batteries-included.
- **Signed native bundles** — per-OS, double-click audience, batteries-included.

## What GitHub covers

- **Source / issues / PRs** — the repo.
- **Docs** — GitHub Pages (MkDocs Material pointed at `docs/`), built by Actions.
- **Builds (CI)** — GitHub Actions; mac + windows + old-Linux runners run PyInstaller.
- **Binaries** — GitHub Releases hosts the DMG / installer / AppImage, attached on tag.
- **Packages** — GitHub *Packages* registry does **not** support PyPI (only npm, Docker,
  Maven, NuGet, RubyGems). So Python distribution goes to **PyPI**, not GitHub Packages.
  `ghcr.io` is the GitHub-native registry *if* we ever ship a Docker image.

## PyPI

- The canonical home for `pip install yaffo`. Cross-platform via **wheels**; yaffo's
  compiled deps (`onnxruntime`, `opencv-python`, `Pillow`, `numpy`, `scipy`) all publish
  wheels for macOS (Intel + Apple Silicon), Windows x86_64, and Linux, so users never compile.
- **Not batteries-included — two prerequisites the bundles don't have:**
  1. **Python 3.13** must already be installed (`requires-python = "~=3.13.0"`). pip does
     not bundle the interpreter; the PyInstaller DMG/exe/AppImage do.
  2. **exiftool** is a Perl program, *not* a PyPI package — it will never come from pip.
     Must be installed out-of-band (`brew install exiftool` /
     `apt install libimage-exiftool-perl` / `winget install exiftool`). The PyInstaller
     bundles can embed it; pip can't. (Also confirm CLIP / InsightFace weights download
     on first run — needs network the first time.)
- **Use Trusted Publishing (PEP 740), not a stored API token.** Upload via OIDC from
  GitHub Actions — no long-lived `PYPI_API_TOKEN` secret, and it attaches a provenance
  attestation automatically. Implemented in `.github/workflows/release.yml` (triggers on
  a `v*` tag; the `publish` job uses `environment: pypi` + `id-token: write`).
- **Version is single-sourced from the `VERSION` file.** `pyproject.toml` reads it via
  `[tool.setuptools.dynamic] version = {file = "VERSION"}` (no more static-version drift),
  and `yaffo/version.py` falls back VERSION-file → installed metadata → `0.0.0`. To cut a
  release, run the `Release` workflow manually: it bumps `VERSION`, commits `Release vX.Y.Z`,
  creates/pushes the `vX.Y.Z` tag, builds Python artifacts and the macOS DMG, attaches them
  to a GitHub Release, then publishes PyPI. A direct `v*` tag push is still supported, but
  the workflow guards that the tag matches the committed `VERSION`.

### Install from PyPI and run

`pip install yaffo` installs the Python package and exposes a `yaffo` command via
`[project.scripts]`. The command starts `python -m yaffo` in the background, then
returns the shell to the user. The background process migrates the app database,
starts the web server on `127.0.0.1:5001`, starts the task-queue host and filesystem
watcher children, and opens the browser.

```shell
python3.13 -m venv ~/.venvs/yaffo
source ~/.venvs/yaffo/bin/activate
python -m pip install --upgrade pip
python -m pip install yaffo

yaffo
```

For end users, prefer `pipx install yaffo` so Yaffo lives in an isolated
environment while the `yaffo` command is exposed globally. After install, users
can create a clickable launcher:

```shell
yaffo install-shortcut
```

That writes a per-user OS launcher: a macOS `.app` wrapper on the Desktop, a
Windows `.lnk` on the Desktop, or a Linux `~/.local/share/applications/yaffo.desktop`
entry. Shortcuts call the installed interpreter with `-m yaffo.launcher` so they do
not depend on an interactive shell's `PATH`. `[project.gui-scripts]` also exposes
`yaffo-gui`, primarily to avoid a console window on Windows.

Foreground/debug fallback if the console script is unavailable:

```shell
python -m yaffo
```

Data lives in the OS user-data directory by default, for example
`~/Library/Application Support/yaffo` on macOS. Override it when testing or when
you want an explicit app-state directory:

```shell
YAFFO_DATA_DIR="$HOME/Pictures/Yaffo State" yaffo
```

### Test a PyPI build locally without publishing

Build the same wheel artifact that GitHub Actions uploads, then install that wheel into a
clean environment. This catches missing subpackages, templates, static files, migrations,
and entry points before publishing.

```shell
rm -rf dist build *.egg-info
python -m pip install --upgrade build
python -m build

python3.13 -m venv /tmp/yaffo-wheel-test
source /tmp/yaffo-wheel-test/bin/activate
python -m pip install --upgrade pip
python -m pip install dist/yaffo-*.whl

python -c "import yaffo.routes, yaffo.taskq.host, yaffo.scripts.db.migrate"
YAFFO_DATA_DIR=/tmp/yaffo-wheel-state yaffo
```

Then open `http://127.0.0.1:5001`. For a CLI-only smoke test, run:

```shell
YAFFO_DATA_DIR=/tmp/yaffo-wheel-state python -m yaffo
```

For the isolated-app workflow Linux users will actually use, test with `pipx`:

```shell
pipx install --python python3.13 --force dist/yaffo-*.whl
pipx runpip yaffo show yaffo
yaffo
```

### Test the native bundled app locally

The PyPI wheel is not the same artifact as the PyInstaller app bundle. To test the
native macOS bundle before attaching it to a GitHub Release:

```shell
./packaging/build_dmg.sh
open dist/
```

Run the generated `.app` or mount the generated `.dmg` and launch the app from there.
The native bundle should embed its own Python runtime and bundled tools; the PyPI wheel
intentionally does not.

In CI, `release.yml` builds the same DMG on `macos-latest` with
`YAFFO_SKIP_VERSION_BUMP=1`, so the DMG version matches the release tag instead of
being incremented a second time by `build_dmg.sh`.

### Install ExifTool

ExifTool is an external prerequisite for PyPI / pipx installs. Verify it with
`exiftool -ver` after installation.

```shell
# macOS Homebrew
brew install exiftool

# macOS MacPorts
sudo port install p5-image-exiftool

# Debian / Ubuntu
sudo apt update
sudo apt install libimage-exiftool-perl

# Fedora
sudo dnf install perl-Image-ExifTool

# Arch Linux
sudo pacman -S perl-image-exiftool

# Windows winget
winget install OliverBetz.ExifTool

# Windows Chocolatey
choco install exiftool

# Windows Scoop
scoop install exiftool
```

### One-time PyPI Trusted Publisher setup (manual)

Before the first release, register a *pending publisher* on PyPI so OIDC is trusted:
PyPI → Account → **Publishing** → add a GitHub publisher with:

- **PyPI Project Name:** `yaffo`
- **Owner:** `Jason-Turan0`  •  **Repository:** `yaffo`
- **Workflow name:** `release.yml`
- **Environment name:** `pypi`

(Optional: do the same on TestPyPI first for a dry run.) After the project exists, convert
it to a normal trusted publisher under the project's settings.
- **Why PyPI requires no code signing:** different trust model. OS gatekeepers vet the
  *publisher's identity* (per-file signature + CA/Apple KYC) because a user runs an opaque
  binary from the open web. PyPI's trust anchor is the *index over TLS* + hash pinning
  (`--require-hashes`) for integrity, on an open index where nobody's identity is vetted.
  PyPI removed GPG upload signatures in 2023 (no identity infra behind the keys); the
  modern direction is OIDC **provenance** (which CI built it from which repo) via Sigstore,
  not developer-held certs.

## macOS — Homebrew cask + signing

- yaffo already ships a PyInstaller `.app`/DMG (`packaging/build_dmg.sh`). The right
  Homebrew mechanism is a **cask** (prebuilt GUI app), not a formula (source/CLI).
- **Own tap first** (`brew tap <user>/yaffo && brew install --cask yaffo`). The official
  homebrew-cask repo has a notability bar *and requires signing+notarization* — not a v1.
- Cask references the GitHub Release DMG URL + sha256. CI can auto-bump the cask after the
  Release uploads (compute sha256, commit to the tap repo) so a release stays one tag push.

### Signing = two separate gates (both needed for zero warnings)

1. **Code signing** — Apple Developer Program ($99/yr) → *Developer ID Application* cert →
   `codesign` with **hardened runtime** (`--options runtime`).
   - PyInstaller trap: the bundle is the interpreter + dozens of nested `.dylib`/`.so`
     (onnxruntime, opencv, Pillow). Sign **inside-out** — every nested Mach-O, then the
     `.app`. `--deep` is unreliable for deep nesting; walk the tree explicitly.
   - **entitlements.plist** must allow what hardened-runtime blocks for CPython:
     `com.apple.security.cs.allow-unsigned-executable-memory` and
     `com.apple.security.cs.disable-library-validation` (we load dylibs we didn't sign).
     Missing these → signed app that SIGKILLs on launch.
2. **Notarization** — `xcrun notarytool submit … --wait`, then `xcrun stapler staple` so it
   validates offline.

- **CI:** import Developer ID `.p12` (base64 secret) into a temp keychain; use an App Store
  Connect API key for notarytool. **`rcodesign`** (Rust apple-codesign) can sign+notarize
  **from Linux** — no mac runner needed.

## Windows — installer + signing

- **Unsigned UX is doable but ugly** — three gates, one of which hides the way forward:
  1. Browser SmartScreen at download ("isn't commonly downloaded" → `…` → Keep → Keep anyway).
  2. Defender SmartScreen on launch: "Windows protected your PC", only visible button is
     **Don't run**; "Run anyway" is hidden behind **More info**.
  3. UAC elevation showing **Publisher: Unknown**.
- **PyInstaller landmine — antivirus false positives.** PyInstaller `.exe`s frequently get
  flagged as malware (the bootloader unpacks an interpreter to temp = dropper-like
  heuristics), and can be *silently quarantined* with no click-path. Mitigate: sign; submit
  to Microsoft's false-positive portal + AV vendors; ship an **installer** (Inno/NSIS/MSI)
  not a bare `.exe`; prefer `--onedir` over `--onefile`.
- **Signing:** Authenticode via `signtool`. Since June 2023 the key **must be on hardware**
  (HSM/token), so naive CI signing is out. Use **Azure Trusted Signing** (~$10/mo, OV cert,
  reputation builds over time) over the legacy ~$300/yr EV-token route. No notarization step;
  SmartScreen reputation accrues with download volume (EV = instant, OV = gradual).
- **Decision:** reasonable to ship Windows **unsigned with a documented "Windows will warn
  you" section (with the More info → Run anyway screenshots)** for v1; add Trusted Signing
  once there's a user base. AV false positives are the bigger worry than the warnings.

## Linux — no signing anxiety, packaging fragmentation instead

- No Gatekeeper/SmartScreen equivalent; trust = package manager + GPG-signed repos.
- **Lead with `pipx install yaffo`** — Linux users are pip-comfortable, idiomatic for a
  Python *app*, no signing, no sandbox. (Same Python-3.13 + exiftool prereqs as PyPI above.)
- **AppImage** on the GitHub Release for the non-pip crowd — single chmod-+x executable,
  the closest analog to the DMG/portable-exe. No central store, no auto-update.
- **Flathub** = the "brew cask of Linux" (highest discoverability) — *later*, because the
  **sandbox** needs real work for a photo app: must grant `~/Pictures` etc. access via
  `--filesystem=` or XDG portals. Snap is similar/Ubuntu-centric; skip unless we want the
  Ubuntu Software listing. **AUR** `PKGBUILD` is a cheap win for Arch users.
- **PyInstaller/glibc trap:** glibc is backward- but **not forward**-compatible. Build the
  Linux binary on the **oldest** distro we support (old Ubuntu LTS in CI), or it dies with
  `GLIBC_2.xx not found` on older systems. Skip `.deb`/`.rpm` until there's demand.

## Suggested implementation order

1. **PyPI packaging + GitHub Actions release workflow (Trusted Publishing).** Highest
   leverage — one tag-driven pipeline feeds the cask, AppImage, and pipx stories.
2. **MkDocs Material → GitHub Pages** from `docs/` (lowest risk).
3. **macOS sign + notarize + staple** in `packaging/build_dmg.sh` + `entitlements.plist`
   (only the $99 cost; `rcodesign` avoids needing a mac runner). Then the **Homebrew tap/cask**.
4. **AppImage** (built on old-LTS) attached to the Release.
5. **Windows:** ship unsigned + warning docs; add **Azure Trusted Signing** later.
6. **Flathub / AUR** once there's a user base.

## Signing cost summary

| Platform | Cost | Mechanism |
|----------|------|-----------|
| macOS | $99/yr | Apple Developer Program — sign + notarize |
| Windows | ~$10/mo | Azure Trusted Signing (or ~$300/yr legacy EV token) |
| Linux | $0 | no per-binary signing |
