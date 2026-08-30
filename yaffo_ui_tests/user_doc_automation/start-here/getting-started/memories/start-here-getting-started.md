# start-here/getting-started

Non-visual dependency check flags when `pyproject.toml` changes. The only prose the
page derives from pyproject.toml is the install prerequisite "Python 3.13" (package
name `yaffo` and the `yaffo` / `yaffo setup` entry points too).

Verified against unchanged source (launcher.py, setup.py, __main__.py, app.py):
- package name `yaffo`, commands `yaffo`, `yaffo setup`, `yaffo uninstall`
- default port 5001
- teardown: setup downloads ExifTool/InsightFace/CLIP/ffmpeg; installs desktop shortcut
- tray/menu icon per OS

pyproject.toml is NOT readable from the allowed directories (repo root outside
allowed paths), so the requires-python value can't be read directly here. The app
runtime in this environment is Python 3.13 (cpython-313 pyc files).
