import io
import uuid
from dataclasses import dataclass
from src.unit_of_work import UnitOfWorkInterface
from typing import Callable

import numpy as np

from shared.events import (
    EXCHANGE_NAME,  # noqa: F401 — re-exported by design
    ROUTING_KEY_MAP_CREATED,
    MapCreated,
)

from src.core.generator import PathformerMapGenerator

@dataclass
class GenerateResult:
    map_id: str
    grid_size: tuple[int, int]


class GenerateMapUseCase:
    def __init__(
        self,
        generator: PathformerMapGenerator,
        uow_factory: Callable[[], UnitOfWorkInterface]  ,
        bucket: str,
        max_current: float = 3.0,
    ) -> None:
        self._generator = generator
        self._bucket = bucket
        self._max_current = max_current
        self._uow_factory = uow_factory

    async def execute(self, height: int, width: int, seed: int) -> GenerateResult:
        async with self._uow_factory() as uow:

            arrays = self._generator.generate(height, width, seed)

            npz_bytes = self._serialize_npz(arrays)

            map_id = str(uuid.uuid4())
            key = self.create_key(map_id)

            await uow.upload(self._bucket, key, npz_bytes)

            event = MapCreated(
                map_id=map_id,
                bucket=self._bucket,
                key=key,
                grid_size=(height, width),
                max_current=self._max_current,
                seed=seed,
            )
            await uow.publish(ROUTING_KEY_MAP_CREATED, event.model_dump())

            return GenerateResult(map_id=map_id, grid_size=(height, width))

    @staticmethod
    def _serialize_npz(arrays: dict) -> bytes:
        buf = io.BytesIO()
        np.savez_compressed(buf, **arrays)
        return buf.getvalue()

    @staticmethod
    def create_key(map_id: str) -> str:
        return f"{map_id}.npz"
