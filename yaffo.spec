# PyInstaller spec for Yaffo — a macOS .app bundle.
#
# Build:  pyinstaller --noconfirm yaffo.spec   (packaging/build_dmg.sh does this)
#
# The task system is multi-process (a host that spawns workers). PyInstaller's
# multiprocessing support + multiprocessing.freeze_support() in yaffo/__main__.py
# make that work: a spawned worker re-execs this frozen binary and the bootloader
# runs the worker instead of the app.
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = []
binaries = []
hiddenimports = []

# Native / data-heavy packages PyInstaller can't fully trace on its own (scipy
# ships Cython helpers like scipy._cyutility that the default hook misses).
for pkg in ("onnxruntime", "insightface", "cv2", "pillow_heif", "scipy"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# Pull in dynamically-imported submodules: our own package (routes/tasks/automations
# are imported by name at runtime), sklearn (clustering), and explicit stragglers.
hiddenimports += collect_submodules("yaffo")
hiddenimports += collect_submodules("sklearn")
hiddenimports += ["starlark", "keyring.backends.macOS", "waitress"]

# App data files (kept out of the PYZ so they exist on disk in the bundle).
datas += [
    ("yaffo/templates", "yaffo/templates"),
    ("yaffo/static", "yaffo/static"),
    ("yaffo/scripts/db/migrations", "yaffo/scripts/db/migrations"),  # loaded by path
    ("resources", "resources"),  # exiftool + bundled models + THIRD_PARTY_LICENSES
]

a = Analysis(
    ["yaffo/__main__.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PyQt5", "PyQt6", "PySide2", "PySide6"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Yaffo Photo Organizer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    target_arch="arm64",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Yaffo Photo Organizer",
)

app = BUNDLE(
    coll,
    name="Yaffo Photo Organizer.app",
    icon=None,
    bundle_identifier="com.jasonturan.yaffo",
    version="0.0.1",
    info_plist={
        "CFBundleName": "Yaffo Photo Organizer",
        "CFBundleDisplayName": "Yaffo Photo Organizer",
        "CFBundleShortVersionString": "0.0.1",
        "CFBundleVersion": "0.0.1",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "14.0",
    },
)
