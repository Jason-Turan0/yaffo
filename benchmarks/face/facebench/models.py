"""Fetch the ONNX model files the OpenCV backends need (InsightFace downloads its
own packs to ~/.insightface). Pulled from the OpenCV Zoo via the git-LFS media host
and cached under benchmarks/face/models/."""
from __future__ import annotations

import urllib.request
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"

# git-LFS content is served from media.githubusercontent.com (raw returns a pointer).
_OPENCV_ZOO = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models"
MODEL_URLS = {
    "yunet": f"{_OPENCV_ZOO}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "sface": f"{_OPENCV_ZOO}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
}


def ensure_model(key: str) -> Path:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    url = MODEL_URLS[key]
    dest = MODELS_DIR / Path(url).name
    if dest.exists() and dest.stat().st_size > 10_000:
        return dest
    print(f"downloading {key} model -> {dest}")
    urllib.request.urlretrieve(url, dest)
    if dest.stat().st_size < 10_000:
        raise RuntimeError(
            f"{dest} looks like an LFS pointer, not the model. Download it manually "
            f"from {url} (or the OpenCV Zoo repo) into {MODELS_DIR}."
        )
    return dest
