"""Compatibility wrapper for the app-owned asset downloader."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from yaffo.download_assets import main


if __name__ == "__main__":
    sys.exit(main())
