"""Arc-length trajectory resampling.

Resample a path to have uniformly spaced waypoints along the arc length.
The neural network expects trajectories with a fixed, uniform structure.
"""
from __future__ import annotations

import numpy as np


def resample_trajectory(
    path: np.ndarray,
    target_step: float = 3.0,
    min_pts: int = 10,
    max_pts: int = 200,
) -> np.ndarray | None:
    """Resample a path to uniform arc-length spacing.

    Parameters
    ----------
    path:
        Input path as ndarray of shape (N, 2), where each row is (row, col).
    target_step:
        Desired spacing between waypoints in cells.
    min_pts:
        Minimum number of waypoints in the result (clamped).
    max_pts:
        Maximum number of waypoints in the result (clamped).

    Returns
    -------
    np.ndarray of shape (N_new, 2) with dtype float32, or None if the path
    is degenerate (fewer than 2 points or zero arc length).
    """
    path = np.asarray(path, dtype=np.float64)

    # Need at least 2 points to define an arc
    if path.ndim != 2 or path.shape[0] < 2 or path.shape[1] < 2:
        return None

    # Compute segment lengths
    segments = np.diff(path, axis=0)
    lengths = np.linalg.norm(segments, axis=1)

    # Cumulative arc distances, starting from 0
    cum_dists = np.concatenate([[0.0], np.cumsum(lengths)])
    total = cum_dists[-1]

    # Degenerate: zero or non-finite arc length
    if total == 0.0 or not np.isfinite(total):
        return None

    # Determine number of output points
    n_new = int(np.clip(round(total / target_step) + 1, min_pts, max_pts))

    # Target distances uniformly spaced over [0, total]
    target_dists = np.linspace(0.0, total, n_new)

    # Interpolate each coordinate independently
    rows = np.interp(target_dists, cum_dists, path[:, 0])
    cols = np.interp(target_dists, cum_dists, path[:, 1])

    result = np.column_stack([rows, cols]).astype(np.float32)

    # Force exact endpoints to avoid floating-point drift
    result[0] = path[0].astype(np.float32)
    result[-1] = path[-1].astype(np.float32)

    return result
