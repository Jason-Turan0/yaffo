"""Zero-shot image labeling via CLIP (ViT-B/32, OpenAI weights) on onnxruntime.

Two ONNX encoders map images and label texts into a shared 512-d space; cosine
similarity (a dot product, since both are L2-normalized) scores how well a label
describes a photo. Mirrors utils.face_analysis: CPU-only, lazy per-process
singletons, and the model auto-downloads on first use — here to common.MODEL_CACHE_DIR
rather than ~/.insightface. The tokenizer is vendored (utils.clip_tokenizer), so
no torch/transformers is pulled in.

The encoders come from immich's pinned ONNX export of open_clip ViT-B-32__openai
(image input [1,3,224,224] float -> [1,512]; text input [1,77] int32 -> [1,512]).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from yaffo.common import BUNDLED_MODELS_DIR, MODEL_CACHE_DIR
from yaffo.logging_config import get_logger
from yaffo.utils.clip_tokenizer import tokenize

logger = get_logger(__name__, "background_tasks")

MODEL_NAME = "ViT-B-32__openai"
EMBEDDING_DIM = 512
_IMAGE_SIZE = 224
# OpenAI CLIP preprocessing constants (immich visual/preprocess_cfg.json).
_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)

_HF_BASE = "https://huggingface.co/immich-app/ViT-B-32__openai/resolve/main"
_FILES = {"visual": "visual/model.onnx", "textual": "textual/model.onnx"}

_visual = None
_textual = None

# The practical range for OpenAI CLIP ViT-B/32 cosine similarity scores, mapped onto
# the 0-100 strictness slider. 0.18 is the noise floor (slider 0, high recall); 0.298
# is the ceiling (slider 100, high precision). The band is centred so the neutral
# midpoint (slider 50) lands on ~0.239 cosine — the measured sweet spot where most
# photos get their top label while weak matches are dropped.
CLIP_SIMILARITY_FLOOR = 0.18
CLIP_SIMILARITY_CEILING = 0.298

def get_clip_threshold(ui_threshold: int) -> float:
    """Maps a 0-100 strictness slider to the practical CLIP similarity range."""
    ui_threshold = max(0, min(100, ui_threshold))

    # Direct linear mapping: higher slider = higher mathematical threshold
    range_width = CLIP_SIMILARITY_CEILING - CLIP_SIMILARITY_FLOOR
    return CLIP_SIMILARITY_FLOOR + (ui_threshold / 100.0) * range_width

def _model_dir() -> Path:
    """Prefer the encoders bundled into the app (no network on first run);
    otherwise use the cache dir, where `_ensure_model` downloads them on demand."""
    bundled = BUNDLED_MODELS_DIR / "clip" / MODEL_NAME
    if (bundled / _FILES["visual"]).is_file():
        return bundled
    return Path(MODEL_CACHE_DIR) / "clip" / MODEL_NAME


def _ensure_model(kind: str) -> Path:
    """Path to the `kind` encoder, downloading it to the cache on first use."""
    path = _model_dir() / _FILES[kind]
    if path.is_file():
        return path
    import requests

    path.parent.mkdir(parents=True, exist_ok=True)
    url = f"{_HF_BASE}/{_FILES[kind]}"
    logger.info(f"downloading CLIP {kind} encoder -> {path}")
    tmp = path.with_suffix(".onnx.part")
    with requests.get(url, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        with open(tmp, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                fh.write(chunk)
    tmp.rename(path)
    return path


def _session(kind: str):
    import onnxruntime as ort

    return ort.InferenceSession(str(_ensure_model(kind)), providers=["CPUExecutionProvider"])


def _get_visual():
    global _visual
    if _visual is None:
        logger.info(f"loading CLIP visual encoder '{MODEL_NAME}' (CPU)")
        _visual = _session("visual")
    return _visual


def _get_textual():
    global _textual
    if _textual is None:
        logger.info(f"loading CLIP text encoder '{MODEL_NAME}' (CPU)")
        _textual = _session("textual")
    return _textual


def _normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=-1, keepdims=True)
    return vectors / np.clip(norms, 1e-12, None)


def _preprocess(image_rgb: np.ndarray) -> np.ndarray:
    """Resize (shortest side -> 224, bicubic), center-crop, normalize to a
    (1,3,224,224) float32 tensor — the OpenAI CLIP image pipeline."""
    from PIL import Image

    img = Image.fromarray(np.ascontiguousarray(image_rgb)).convert("RGB")
    w, h = img.size
    scale = _IMAGE_SIZE / min(w, h)
    img = img.resize((round(w * scale), round(h * scale)), Image.BICUBIC)
    nw, nh = img.size
    left, top = (nw - _IMAGE_SIZE) // 2, (nh - _IMAGE_SIZE) // 2
    img = img.crop((left, top, left + _IMAGE_SIZE, top + _IMAGE_SIZE))
    arr = (np.asarray(img, dtype=np.float32) / 255.0 - _MEAN) / _STD
    return np.ascontiguousarray(arr.transpose(2, 0, 1)[None], dtype=np.float32)


def embed_image(image_rgb: np.ndarray) -> np.ndarray:
    """A 512-d L2-normalized CLIP embedding for one RGB (HWC uint8) image. A
    degenerate crop that yields a non-finite vector returns zeros (cosine 0, so no
    label matches) rather than poisoning the similarity math."""
    session = _get_visual()
    out = np.asarray(session.run(None, {session.get_inputs()[0].name: _preprocess(image_rgb)})[0][0], dtype=np.float32)
    if not np.all(np.isfinite(out)):
        return np.zeros(EMBEDDING_DIM, dtype=np.float32)
    return _normalize(out)


def embed_texts(texts: list[str]) -> np.ndarray:
    """An (N, 512) array of L2-normalized CLIP embeddings, one per label text.
    The text encoder is fixed-batch-1, so this loops (label sets are small)."""
    session = _get_textual()
    name = session.get_inputs()[0].name
    tokens = tokenize(texts)
    rows = [session.run(None, {name: tokens[i : i + 1]})[0][0] for i in range(len(texts))]
    return _normalize(np.asarray(rows, dtype=np.float32))
