# Distribution & Packaging Decision Record

> **Status: current plan, not user-facing install docs.** Yaffo will distribute
> through PyPI for now, with `pipx install yaffo` as the preferred end-user
> install path and `yaffo setup` as the runtime setup command. Native macOS DMG,
> Windows installer, and Linux AppImage/Flathub work is deferred until there is
> demand for a double-click install.

## Current Decision

Use one cross-platform Python distribution channel:

| Platform | Supported path | Runtime included? | External prerequisites |
|----------|----------------|-------------------|------------------------|
| macOS | `pipx install yaffo` | no | Python 3.13 |
| Windows | `pipx install yaffo` | no | Python 3.13 |
| Linux | `pipx install yaffo` | no | Python 3.13 |
| Developers | `pip install -e .` | no | Python 3.13 |

Why this is the right first distribution path:

- one build and release workflow covers every desktop OS;
- the package uses published wheels for heavy compiled dependencies such as
  `onnxruntime`, `opencv-python`, `Pillow`, `numpy`, and `scipy`;
- `pipx` gives end users an isolated environment while exposing the `yaffo`
  command globally;
- `yaffo setup` can perform the runtime installation steps that a native
  installer would otherwise own;
- signing, notarization, SmartScreen reputation, antivirus false positives, and
  Linux packaging fragmentation can wait.

This does mean the install is not fully batteries-included. Users must have
Python 3.13 before installing Yaffo, while app-managed runtime assets are
downloaded by setup or startup.

## PyPI / pipx Install Flow

`pipx install yaffo` installs the Python package in an isolated environment and
exposes the `yaffo` command. After install, users should run:

```shell
yaffo setup
```

Setup can:

- write a per-user OS launcher;
- run database migrations;
- download runtime assets synchronously;
- ask whether to launch the app.

Runtime startup still runs migrations and checks runtime assets, so direct
`yaffo` launches remain self-healing. Shortcuts call the installed interpreter
with `-m yaffo.launcher` so they do not depend on an interactive shell's `PATH`.
`[project.gui-scripts]` also exposes `yaffo-gui`, primarily to avoid a console
window on Windows.

For users who do not want `pipx`, a virtual environment is supported:

```shell
python3.13 -m venv ~/.venvs/yaffo
source ~/.venvs/yaffo/bin/activate
python -m pip install --upgrade pip
python -m pip install yaffo
yaffo setup
```

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

To remove app-managed files before removing the pipx environment:

```shell
yaffo uninstall
pipx uninstall yaffo
```

Uninstall removes the shortcut, downloaded assets, and logs, then asks before
removing Yaffo user data such as databases and config.

## Runtime Prerequisites

### Python

Yaffo requires Python 3.13 (`requires-python = "~=3.13.0"`). pip and pipx do not
bundle the interpreter. Native installers may eventually embed Python, but that
is not part of the current distribution plan.

### Runtime Assets

ExifTool, CLIP, and InsightFace assets are app-managed runtime downloads, not
manual user prerequisites. `yaffo setup` should do that work up front when
possible so the first app launch is predictable. Runtime startup still verifies
assets and downloads missing files when needed.

ExifTool is not a PyPI dependency, so pip and pipx do not install it as a normal
package dependency. Yaffo downloads an app-managed ExifTool copy under the
runtime asset directory and resolves metadata reads/writes through that binary.

## Publishing

PyPI is the canonical home for `pip install yaffo`. GitHub Packages does not
support PyPI packages, so Python distribution goes to PyPI rather than GitHub
Packages.

PyPI publishing is already configured through GitHub Actions using Trusted
Publishing. The workflow uploads via OIDC, so there is no long-lived
`PYPI_API_TOKEN` secret, and PyPI can attach a provenance attestation. The
release workflow uses the `pypi` environment plus `id-token: write`.

Versioning is single-sourced from the `VERSION` file. `pyproject.toml` reads it
via `[tool.setuptools.dynamic] version = {file = "VERSION"}`, and
`yaffo/version.py` falls back through VERSION file, installed metadata, and
`0.0.0`.

The PyPI trusted publisher is already configured as:

- **PyPI Project Name:** `yaffo`
- **Owner:** `Jason-Turan0`
- **Repository:** `yaffo`
- **Workflow name:** `release.yml`
- **Environment name:** `pypi`

PyPI does not require OS code signing. Its trust model is the package index over
TLS, optional hash pinning for consumers, and OIDC provenance for releases. OS
gatekeepers use a different model because users run opaque binaries downloaded
from the web; that model applies to future native installers, not the current
pipx distribution.

## Local Release Testing

Build the same wheel artifact that GitHub Actions uploads, then install that
wheel into a clean environment. This catches missing subpackages, templates,
static files, migrations, and entry points before publishing.

```shell
rm -rf dist build *.egg-info
python -m pip install --upgrade build
python -m build

python3.13 -m venv /tmp/yaffo-wheel-test
source /tmp/yaffo-wheel-test/bin/activate
python -m pip install --upgrade pip
python -m pip install dist/yaffo-*.whl

python -c "import yaffo.routes, yaffo.taskq.host, yaffo.scripts.db.migrate"
YAFFO_DATA_DIR=/tmp/yaffo-wheel-state yaffo setup
YAFFO_DATA_DIR=/tmp/yaffo-wheel-state yaffo
```

Then open `http://127.0.0.1:5001`. For a CLI-only smoke test, run:

```shell
YAFFO_DATA_DIR=/tmp/yaffo-wheel-state python -m yaffo
```

For the isolated-app workflow users will actually use, test with `pipx`:

```shell
pipx install --python python3.13 --force dist/yaffo-*.whl
pipx runpip yaffo show yaffo
yaffo setup
yaffo
```

## Deferred Native Installers

Native installers are explicitly out of scope for the current distribution
plan. Keep the existing PyInstaller and packaging code only as experimental
infrastructure unless a release task says otherwise.

Future macOS work:

- signed and notarized `.app`/DMG;
- Homebrew cask in an owned tap;
- Developer ID certificate, hardened runtime entitlements, inside-out signing
  for nested `.dylib`/`.so` files, notarization, and stapling.

Future Windows work:

- signed installer rather than a bare PyInstaller `.exe`;
- SmartScreen and UAC warning documentation if shipping unsigned;
- antivirus false-positive mitigation;
- Azure Trusted Signing or another Authenticode signing path.

Future Linux work:

- AppImage only if there is demand for non-pip installs;
- Flathub only after solving filesystem access for a photo-management app;
- AUR package if Arch users ask for it;
- old-LTS build environment if producing glibc-linked native binaries.

Do not prioritize DMG, EXE, AppImage, Flathub, Homebrew cask, or winget work
until the pipx distribution and runtime setup flow are solid.
