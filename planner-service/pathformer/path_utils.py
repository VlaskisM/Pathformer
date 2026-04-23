import numpy as np
from scipy.interpolate import CubicSpline


def smooth_path(waypoints: np.ndarray, factor: int = 2) -> np.ndarray:
    """Cubic spline smoothing of a path.

    Args:
        waypoints: (N, 2) path waypoints.
        factor: Upsampling factor for interpolation.

    Returns:
        (N*factor, 2) smoothed path.
    """
    if len(waypoints) < 3:
        return waypoints

    t = np.arange(len(waypoints), dtype=np.float64)
    t_new = np.linspace(0, len(waypoints) - 1, len(waypoints) * factor)

    cs_r = CubicSpline(t, waypoints[:, 0])
    cs_c = CubicSpline(t, waypoints[:, 1])

    smoothed = np.column_stack([cs_r(t_new), cs_c(t_new)])
    return smoothed.astype(np.float32)


def resample_path(waypoints: np.ndarray, n_points: int) -> np.ndarray:
    """Resample path to fixed number of evenly-spaced points.

    Args:
        waypoints: (N, 2) path waypoints.
        n_points: Desired number of output points.

    Returns:
        (n_points, 2) resampled path.
    """
    if len(waypoints) < 2:
        return np.repeat(waypoints, n_points, axis=0)

    # Compute cumulative arc length
    diffs = np.diff(waypoints, axis=0)
    seg_lengths = np.linalg.norm(diffs, axis=1)
    cum_len = np.concatenate([[0], np.cumsum(seg_lengths)])
    total_len = cum_len[-1]

    if total_len < 1e-10:
        return np.repeat(waypoints[:1], n_points, axis=0)

    # Evenly spaced arc-length values
    target_lens = np.linspace(0, total_len, n_points)

    # Interpolate
    resampled = np.zeros((n_points, 2), dtype=np.float32)
    for dim in range(2):
        resampled[:, dim] = np.interp(target_lens, cum_len, waypoints[:, dim])

    return resampled
