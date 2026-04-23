"""Start/goal sampling with connectivity check.

Provides sample_start_goal() for picking valid navigation pairs on a cost map.
Includes a cached version that precomputes connected components once per map.
"""
import math

import numpy as np
from scipy.ndimage import label


def _precompute_components(cost_map: np.ndarray):
    """Precompute water mask, connected component labels, and per-component coords.

    Returns
    -------
    water_coords : np.ndarray (K, 2)
        All water cell coordinates.
    labels : np.ndarray (H, W)
        Connected component labels (0 = land).
    comp_coords : dict[int, np.ndarray]
        Mapping from component label to (N_comp, 2) coordinate array.
    """
    water_mask = np.isfinite(cost_map)
    water_coords = np.argwhere(water_mask)

    if len(water_coords) < 2:
        return water_coords, None, {}

    comp_labels, _ = label(water_mask)

    # Build per-component coord arrays once
    comp_coords = {}
    for lbl in np.unique(comp_labels):
        if lbl == 0:  # background (land)
            continue
        coords = np.argwhere(comp_labels == lbl)
        if len(coords) >= 2:
            comp_coords[lbl] = coords

    return water_coords, comp_labels, comp_coords


def sample_start_goal(
    cost_map: np.ndarray,
    rng: np.random.Generator,
    min_dist: float = 20.0,
    max_attempts: int = 1000,
    *,
    _cache: dict | None = None,
) -> "tuple[tuple[int, int], tuple[int, int]] | None":
    """Sample a valid (start, goal) pair on water in the same connected component.

    Args:
        cost_map: float32 (H, W). Water cells have finite cost (>= 1.0);
                  land cells have cost = inf.
        rng: NumPy Generator for reproducible sampling.
        min_dist: Minimum Euclidean distance between start and goal (pixels).
        max_attempts: Maximum sampling iterations before giving up.
        _cache: Optional dict for caching precomputed components. If provided,
                the function stores/retrieves precomputed data using the cost_map
                id as key. This avoids recomputing label() for every call on the
                same cost_map.

    Returns:
        (start, goal) as (row, col) integer tuples, or None if sampling failed.
    """
    # Use cache if provided, otherwise compute fresh
    cache_key = id(cost_map)
    if _cache is not None and cache_key in _cache:
        water_coords, comp_labels, comp_coords = _cache[cache_key]
    else:
        water_coords, comp_labels, comp_coords = _precompute_components(cost_map)
        if _cache is not None:
            _cache[cache_key] = (water_coords, comp_labels, comp_coords)

    if len(water_coords) < 2 or comp_labels is None:
        return None

    for _ in range(max_attempts):
        idx1 = rng.integers(len(water_coords))
        start = water_coords[idx1]
        comp = comp_labels[start[0], start[1]]

        if comp not in comp_coords:
            continue

        coords = comp_coords[comp]

        idx2 = rng.integers(len(coords))
        goal = coords[idx2]

        dist = math.sqrt((start[0] - goal[0]) ** 2 + (start[1] - goal[1]) ** 2)
        if dist >= min_dist:
            return (tuple(start), tuple(goal))

    return None
