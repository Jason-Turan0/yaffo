# facebench — face library shootout

A standalone harness to compare face **detection** and **recognition** libraries on
the Turan-Benchmark photo set, on **accuracy** and **wall time**. It lives in its own
venv so the heavy/conflicting ML deps (onnxruntime, torch, tensorflow, dlib…) never
touch the main `yaffo` environment.

## Dataset

```
<root>/<person>/<numberOfFaces>/<photo>
```

The folder name is the ground-truth face count for every photo in it. The `1`
folder holds each person's solo **reference** photos; `>1` folders are **group**
photos containing that person plus others. Default root:
`/Users/jason.turan/Pictures/Turan-Benchmark`.

## Setup

```bash
./setup_venv.sh                                   # core: OpenCV + InsightFace
./venv/bin/pip install -r requirements-optional.txt   # + dlib baseline, MediaPipe, FaceNet
```

## Run

```bash
./venv/bin/python run.py                  # all installed backends, full set
./venv/bin/python run.py --limit 20       # quick smoke
./venv/bin/python run.py --backends opencv-yunet-sface,insightface-scrfd-arcface
./venv/bin/python run.py --json results/run.json
```

Backends whose library isn't installed are skipped (the runner says which), so you
can add them incrementally.

## What it measures

**Detection** (count vs folder label): exact-count rate, mean abs error, a face-
recall proxy (`Σ min(detected, expected) / Σ expected`), and ms/photo (detection
timed in isolation, even for fused pipelines).

**Recognition** (the metric you specified): a person's solo reference embedding vs
their group photos — `recall` = fraction of group photos where it matches ≥1
detected face. Plus impostor **FAR** (reference vs *other* people's solo photos),
and threshold-free **AUC** / **EER** so models with differently-calibrated
thresholds compare fairly. Embedding wall time as ms/face.

## Backends

| name | detector | embedder | dep |
|---|---|---|---|
| `dlib-hog-resnet` | dlib HOG | dlib 128-d | dlib (source build) — current baseline |
| `opencv-yunet-sface` | YuNet | SFace 128-d | opencv-contrib-python |
| `insightface-scrfd-arcface` | SCRFD | ArcFace 512-d | insightface + onnxruntime |
| `mediapipe-blazeface` | BlazeFace | — (detector only) | mediapipe |
| `facenet-mtcnn` | MTCNN | FaceNet 512-d | facenet-pytorch + torch |

## Adding a backend

Implement `detect()` / `analyze()` (+ `metric`, `threshold`) in
`facebench/backends.py`, add the class to `ALL_BACKENDS`, and add its dep to a
requirements file. See the existing backends for the shape.

## Latest results (Turan-Benchmark, 202 photos, this machine / CPU)

```
DETECTION (count vs folder label)   exact   MAE  recall  ms/photo
dlib-hog-resnet  (current baseline)   63%   0.47    79%    1790
opencv-yunet-sface                    49%   1.07    91%     127
insightface-scrfd-arcface             85%   0.26    99%      91

RECOGNITION (reference -> group)     recall  FAR    AUC    EER   ms/face
dlib-hog-resnet  (current baseline)    86%   36%  0.848   20%    1348
opencv-yunet-sface                     83%   19%  0.911   16%      85
insightface-scrfd-arcface              98%   19%  0.993    5%     267
```

**Winner: InsightFace (SCRFD + ArcFace).** Best on every accuracy axis *and* ~20x
faster detection than the dlib baseline; ArcFace recognition is far stronger
(AUC 0.993 / EER 5% vs dlib 0.848 / 20%). The 19% FAR is an artifact of the loose
indicative threshold — rank on AUC/EER, then set the operating threshold. (MediaPipe
and facenet-pytorch were skipped: no clean Python 3.13 install.)

## Caveats

- Detection accuracy is **count-based** (the dataset has no per-face boxes), so it
  can't catch a right-count/wrong-faces case. The recognition metric is the real
  end-to-end signal.
- Wall times are this machine, CPU, single-process — for *ranking*, not absolutes.
- Thresholds per model are indicative; rank on AUC/EER, then pick an operating
  threshold per the deployment's recall/FAR tradeoff.
