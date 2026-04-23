"""Converts a raw .npz map (land_mask, current_intensity, current_direction)
into the 3-channel tensor pathformer expects: (u, v, safety_field).

Formula reproduced from pathformer/src/pathformer/data/dataset.py:70-84.
"""

import io

import numpy as np
from scipy.ndimage import distance_transform_edt


def load_arrays(npz_bytes: bytes) -> dict[str, np.ndarray]:
    buf = io.BytesIO(npz_bytes)
    with np.load(buf) as data:
        return {k: data[k] for k in data.files}


def to_model_tensor(
    arrays: dict[str, np.ndarray],
    max_current_global: float,
) -> np.ndarray:
    intensity = arrays["current_intensity"].astype(np.float32)
    direction = arrays["current_direction"].astype(np.float32)
    land_mask = arrays["land_mask"].astype(np.float32)

    # pathformer's PE is precomputed for 16x16 tokens (encoder downsamples 16x),
    # which caps inputs at 256x256. Larger maps would silently crop PE and
    # degrade quality. See pathformer/src/pathformer/model/planner.py:29.
    H, W = land_mask.shape
    if max(H, W) > 256:
        raise ValueError(
            f"Map {H}x{W} exceeds pretrained positional encoding capacity (256x256)."
        )

    u = (intensity * np.cos(direction) / max_current_global).astype(np.float32)
    v = (intensity * np.sin(direction) / max_current_global).astype(np.float32)

    # Distance from every water cell to nearest land, clipped and inverted so
    # 1.0 = on/next-to land, 0.0 = far offshore. Matches training-time code.
    dt = distance_transform_edt(1.0 - land_mask)
    safety_field = (1.0 - np.clip(dt / 20.0, 0.0, 1.0)).astype(np.float32)

    return np.stack([u, v, safety_field], axis=0)
