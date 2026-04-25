import numpy as np


def build_cost_map(
    intensity: np.ndarray,
    land_mask: np.ndarray,
    vessel_max_current: float,
) -> np.ndarray:
    """Строит карту стоимости для класса судна.

    Formula: 1.0 + 1.0*(I/3.0) + 10.0*(I > max_current) + inf*land_mask

    Args:
        intensity: float32 (H, W) -- интенсивность течения [0, 3.0]
        land_mask: float32 (H, W) -- 1.0 суша, 0.0 вода
        vessel_max_current: float -- макс. допустимое течение для класса судна

    Returns:
        float32 (H, W) -- стоимость. Вода >= 1.0, суша = inf.
    """
    base = (
        1.0
        + 1.0 * (intensity / 3.0)
        + 10.0 * (intensity > vessel_max_current).astype(np.float32)
    )
    cost = np.where(land_mask > 0.5, np.inf, base)
    return cost.astype(np.float32)
