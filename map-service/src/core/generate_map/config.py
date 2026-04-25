from dataclasses import dataclass


@dataclass
class MapConfig:
    # Land mask
    land_sigma: float = 20.0        # Sigma для gaussian smoothing шума
    land_fraction: float = 0.15     # Целевая доля суши (~15%)

    # Current intensity
    intensity_sigma: float = 30.0   # Sigma для smoothing intensity noise
    max_intensity: float = 3.0      # Максимальная интенсивность течения м/с

    # Current direction (curl noise)
    curl_sigma: float = 25.0        # Sigma для smoothing потенциального поля

    # Resampling (для будущих фаз, но объявляем сейчас по CFG-01)
    target_step: float = 3.0        # Целевой шаг ресэмплинга
    min_waypoints: int = 10         # Минимум waypoints
    max_waypoints: int = 200        # Максимум waypoints
