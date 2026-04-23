import numpy as np
from .config import MapConfig
from .land import generate_land_mask
from .current_field import generate_intensity, generate_direction


def generate_map(
    height: int,
    width: int,
    seed: int,
    config: MapConfig | None = None,
) -> dict[str, np.ndarray]:
    """Генерирует синтетическую карту течений.

    Returns:
        dict с ключами:
        - 'land_mask': float32 (H, W) — 1.0 суша, 0.0 вода
        - 'current_intensity': float32 (H, W) — [0, max_intensity] м/с, 0 на суше
        - 'current_direction': float32 (H, W) — [0, 2*pi) рад, curl noise
    """
    if config is None:
        config = MapConfig()

    rng = np.random.default_rng(seed)

    # Порядок вызовов фиксирован для воспроизводимости: land → intensity → direction
    land_mask = generate_land_mask(height, width, rng, config)
    current_intensity = generate_intensity(height, width, rng, config, land_mask)
    current_direction = generate_direction(height, width, rng, config)

    return {
        'land_mask': land_mask,
        'current_intensity': current_intensity,
        'current_direction': current_direction,
    }
