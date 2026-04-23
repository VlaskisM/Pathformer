"""GenerateMapUseCase — orchestrates the full /generate flow.

Pure business logic: knows nothing about HTTP, S3, or AMQP. Receives
ports by constructor injection.
"""

import io
import uuid
from dataclasses import dataclass

import numpy as np

from shared.events import (
    EXCHANGE_NAME,  # noqa: F401 — re-exported by design
    ROUTING_KEY_MAP_CREATED,
    MapCreated,
)

from .ports import BrokerPort, MapGeneratorPort, StoragePort


@dataclass
class GenerateResult:
    map_id: str
    grid_size: tuple[int, int]


class GenerateMapUseCase:
    def __init__(
        self,
        generator: MapGeneratorPort,
        storage: StoragePort,
        broker: BrokerPort,
        bucket: str,
        max_current: float = 3.0,
    ) -> None:
        self._generator = generator
        self._storage = storage
        self._broker = broker
        self._bucket = bucket
        self._max_current = max_current

    async def execute(self, height: int, width: int, seed: int) -> GenerateResult:
        arrays = self._generator.generate(height, width, seed)

        npz_bytes = self._serialize_npz(arrays)

        map_id = str(uuid.uuid4())
        key = f"{map_id}.npz"
        await self._storage.upload(self._bucket, key, npz_bytes)

        event = MapCreated(
            map_id=map_id,
            bucket=self._bucket,
            key=key,
            grid_size=(height, width),
            max_current=self._max_current,
            seed=seed,
        )
        await self._broker.publish(ROUTING_KEY_MAP_CREATED, event.model_dump())

        return GenerateResult(map_id=map_id, grid_size=(height, width))

    @staticmethod
    def _serialize_npz(arrays: dict) -> bytes:
        buf = io.BytesIO()
        np.savez_compressed(buf, **arrays)
        return buf.getvalue()
