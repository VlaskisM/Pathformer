import numpy as np


def success_rate(results: list[dict]) -> float:
    """Fraction of paths reaching goal without land collision."""
    if not results:
        return 0.0
    successes = sum(1 for r in results if r["reached_goal"] and not r.get("land_collision", False))
    return successes / len(results)


def cost_ratio(pred_cost: float, gt_cost: float) -> float:
    """cost(pred_path) / cost(RRT*_path). Target <= 1.20."""
    if gt_cost <= 0:
        return float("inf")
    return pred_cost / gt_cost


def frechet_distance(P: np.ndarray, Q: np.ndarray) -> float:
    """Discrete Frechet distance between two paths (iterative DP).

    Args:
        P: (n, 2) first path.
        Q: (m, 2) second path.

    Returns:
        Frechet distance (float).
    """
    n, m = len(P), len(Q)
    dp = np.full((n, m), -1.0)

    def dist(i: int, j: int) -> float:
        return float(np.linalg.norm(P[i] - Q[j]))

    # Fill DP table iteratively
    dp[0, 0] = dist(0, 0)
    for i in range(1, n):
        dp[i, 0] = max(dp[i - 1, 0], dist(i, 0))
    for j in range(1, m):
        dp[0, j] = max(dp[0, j - 1], dist(0, j))
    for i in range(1, n):
        for j in range(1, m):
            dp[i, j] = max(
                min(dp[i - 1, j], dp[i, j - 1], dp[i - 1, j - 1]),
                dist(i, j),
            )

    return float(dp[n - 1, m - 1])


def land_collision_rate(results: list[dict]) -> float:
    """Fraction of paths with >= 1 waypoint on land."""
    if not results:
        return 0.0
    collisions = sum(1 for r in results if r.get("land_collision", False))
    return collisions / len(results)


def stuck_rate(results: list[dict]) -> float:
    """Fraction where stagnation was detected."""
    if not results:
        return 0.0
    stuck = sum(1 for r in results if r.get("stuck", False))
    return stuck / len(results)


def compute_path_cost(
    waypoints: np.ndarray,
    cost_map: np.ndarray,
) -> float:
    """Compute path cost matching the dataset generator cost function.

    cost = sum over segments of: 0.5 * (cost[p0] + cost[p1]) * segment_length
    This matches the A* edge cost formula used in data generation.

    Args:
        waypoints: (N, 2) path in grid coordinates (row, col).
        cost_map: (H, W) cost values per cell.

    Returns:
        Total path cost (float).
    """
    total = 0.0
    H, W = cost_map.shape

    for i in range(1, len(waypoints)):
        p0 = waypoints[i - 1]
        p1 = waypoints[i]

        r0 = int(np.clip(round(p0[0]), 0, H - 1))
        c0 = int(np.clip(round(p0[1]), 0, W - 1))
        r1 = int(np.clip(round(p1[0]), 0, H - 1))
        c1 = int(np.clip(round(p1[1]), 0, W - 1))

        segment_len = float(np.linalg.norm(p1 - p0))
        edge_cost = 0.5 * (float(cost_map[r0, c0]) + float(cost_map[r1, c1])) * segment_len
        total += edge_cost

    return total
