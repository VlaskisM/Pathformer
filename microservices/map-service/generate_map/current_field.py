import numpy as np
from scipy.ndimage import gaussian_filter
from .config import MapConfig


def generate_intensity(
    height: int,
    width: int,
    rng: np.random.Generator,
    config: MapConfig,
    land_mask: np.ndarray,
) -> np.ndarray:
    """Генерирует поле интенсивности течения.

    Returns:
        float32 (H, W) — интенсивность в [0, max_intensity] на воде, 0 на суше.
    """
    noise = rng.standard_normal((height, width))
    smoothed = gaussian_filter(noise, sigma=config.intensity_sigma)
    # Нормализовать в [0, 1] через min-max, затем масштабировать
    mn, mx = smoothed.min(), smoothed.max()
    if mx > mn:
        normalized = (smoothed - mn) / (mx - mn)
    else:
        normalized = np.zeros_like(smoothed)
    intensity = (normalized * config.max_intensity).astype(np.float32)
    # Ноль на суше
    intensity *= (1.0 - land_mask)
    return intensity


def generate_direction(
    height: int,
    width: int,
    rng: np.random.Generator,
    config: MapConfig,
) -> np.ndarray:
    """Генерирует поле направления течения через curl noise.

    Curl noise — перпендикуляр к градиенту сглаженного потенциального поля.
    Гарантирует малую дивергенцию (divergence-free структуру).

    Returns:
        float32 (H, W) — направление в [0, 2*pi) радиан.
    """
    # Потенциальное поле
    noise = rng.standard_normal((height, width))
    potential = gaussian_filter(noise, sigma=config.curl_sigma)
    # Curl: перпендикуляр к градиенту
    # np.gradient возвращает (grad_axis0=y, grad_axis1=x)
    grad_y, grad_x = np.gradient(potential)
    # curl = (-grad_y, grad_x) → direction = atan2(-grad_x, grad_y)
    direction = np.arctan2(-grad_x, grad_y).astype(np.float32)
    # Нормализовать в [0, 2*pi)
    direction = direction % (2 * np.pi)
    return direction.astype(np.float32)
