#!/usr/bin/env python3
"""Turn a selfie into assets/portrait.txt, the ASCII portrait on the whoami card.

Run locally (not in CI) whenever you want a new portrait:

    pip install mediapipe opencv-python-headless numpy
    curl -LO https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_multiclass_256x256/float32/latest/selfie_multiclass_256x256.tflite
    python scripts/make_portrait.py path/to/selfie.jpg --model selfie_multiclass_256x256.tflite

Only the ASCII text is written to the repo; the photo itself never is.
The segmentation model splits the photo into background, hair, skin and
clothes, so the background is dropped and each part gets its own shading.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
RAMP = " .`'^,:;-~+=<>!icoxsvXZ#%&@"
COLS, ROWS = 132, 58          # characters across / lines down
ASPECT = 380 / 314            # width / height of the portrait panel on the card

# selfie_multiclass classes: 0 background, 1 hair, 2 body skin, 3 face skin, 4 clothes, 5 accessories
BG, HAIR, BODY, FACE, CLOTHES, ACC = range(6)


def segment(rgb: np.ndarray, model: str) -> list[np.ndarray]:
    opts = mp.tasks.vision.ImageSegmenterOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=model),
        output_confidence_masks=True,
    )
    with mp.tasks.vision.ImageSegmenter.create_from_options(opts) as seg:
        res = seg.segment(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
    return [m.numpy_view().astype(np.float32).squeeze() for m in res.confidence_masks]


def crop_box(masks, shape):
    """Head-and-shoulders box around the hair + face, at the panel's aspect ratio."""
    head = (masks[HAIR] + masks[FACE]) > 0.5
    ys, xs = np.nonzero(head)
    top, bottom = ys.min(), ys.max()
    head_h = bottom - top
    fx = np.nonzero(masks[FACE] > 0.5)[1]
    cx = (fx.min() + fx.max()) / 2            # centre on the face, not the hair sweep
    y0 = int(top - head_h * 0.04)
    y1 = int(bottom + head_h * 0.16)
    bw = (y1 - y0) * ASPECT
    x0 = int(cx - bw / 2)
    return x0, y0, int(x0 + bw), y1


def take(img: np.ndarray, box, fill=0.0) -> np.ndarray:
    """Crop with padding where the box runs off the photo."""
    x0, y0, x1, y1 = box
    h, w = img.shape[:2]
    out = np.full((y1 - y0, x1 - x0) + img.shape[2:], fill, dtype=img.dtype)
    sx0, sy0, sx1, sy1 = max(x0, 0), max(y0, 0), min(x1, w), min(y1, h)
    out[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] = img[sy0:sy1, sx0:sx1]
    return out


def render(photo: str, model: str, cols: int = COLS, rows: int = ROWS) -> list[str]:
    bgr = cv2.imread(photo)
    rgb = np.ascontiguousarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    masks = segment(rgb, model)
    box = crop_box(masks, rgb.shape[:2])

    gray = cv2.cvtColor(take(bgr, box, 0), cv2.COLOR_BGR2GRAY)
    scale = 1000 / max(gray.shape)
    gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    m = [cv2.resize(take(k, box, 1.0 if i == BG else 0.0), gray.shape[::-1], interpolation=cv2.INTER_AREA)
         for i, k in enumerate(masks)]
    g = cv2.GaussianBlur(gray, (0, 0), 1.2).astype(np.float32) / 255
    texture = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(gray).astype(np.float32) / 255

    # pencil-sketch lines (colour dodge), keeping only the clear strokes
    blur = cv2.GaussianBlur(1 - g, (0, 0), 8)
    lines = 1 - np.clip(g / np.maximum(1 - blur, 1e-3), 0, 1)
    lines = np.clip((lines - 0.06) * 4.0, 0, 1)

    # features (glasses, brows, eyes, beard) are much darker than the skin around them
    skin = (m[FACE] > 0.6)
    ref = float(np.median(g[skin])) if skin.any() else 0.6
    rel = np.clip((ref - g) / (ref * 0.45), 0, 1)

    person = 1 - m[BG]
    ink = (m[HAIR] * (0.45 + 0.55 * (1 - texture)) * (0.6 + 0.4 * rel)
           + m[FACE] * np.maximum(rel ** 1.1, lines * 0.6)
           + m[ACC] * np.maximum(rel, lines)
           + m[BODY] * np.maximum(rel * 0.8, lines * 0.6)
           + m[CLOTHES] * np.maximum(lines * 0.7, (1 - texture) * 0.25))
    # outline of the whole figure so the silhouette always reads
    edge = cv2.morphologyEx((person > 0.5).astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((7, 7), np.uint8))
    ink = np.maximum(ink, edge.astype(np.float32) * 0.6) * np.clip(person * 1.3, 0, 1)

    # fade out the shoulders towards the bottom edge
    fade = np.clip((1 - np.linspace(0, 1, ink.shape[0])) / 0.3, 0, 1)[:, None]
    ink *= fade

    # thicken thin strokes (glasses frames, eyelids) so they survive downsampling
    cell = max(3, int(ink.shape[1] / cols * 0.7)) | 1
    ink = 0.5 * ink + 0.5 * cv2.dilate(ink, np.ones((cell, cell), np.uint8))
    small = cv2.resize(ink, (cols, rows), interpolation=cv2.INTER_AREA)
    small = np.clip(small, 0, 1) ** 0.75
    small[small < 0.12] = 0           # keep the background and bare skin empty
    idx = (small * (len(RAMP) - 1)).round().astype(int)
    return ["".join(RAMP[i] for i in row).rstrip() for row in idx]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("photo")
    ap.add_argument("--model", default="selfie_multiclass_256x256.tflite")
    ap.add_argument("--out", default=str(ROOT / "assets" / "portrait.txt"))
    args = ap.parse_args()
    lines = render(args.photo, args.model)
    Path(args.out).write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
