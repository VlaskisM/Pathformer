import numpy as np
from scipy.ndimage import gaussian_filter
from .config import MapConfig


def generate_land_mask(
    height: int,
    width: int,
    rng: np.random.Generator,
    config: MapConfig,
) -> np.ndarray:
    """Генерирует бинарную маску суши через gaussian-smoothed noise.

    Args:
        height: высота карты в пикселях
        width: ширина карты в пикселях
        rng: генератор случайных чисел (np.random.Generator)
        config: конфигурация карты (используется land_sigma и land_fraction)

    Returns:
        float32 массив (H, W), где 1.0 = суша, 0.0 = вода.
        Доля суши составляет ~config.land_fraction.
    """
    noise = rng.standard_normal((height, width))
    smoothed = gaussian_filter(noise, sigma=config.land_sigma)
    threshold = np.percentile(smoothed, 100 * (1 - config.land_fraction))
    mask = (smoothed >= threshold).astype(np.float32)
    return mask
