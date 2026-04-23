from typing import Protocol

import numpy as np


class StoragePort(Protocol):
    async def upload(self, bucket: str, key: str, data: bytes) -> None: ...
    async def download(self, bucket: str, key: str) -> bytes: ...


class BrokerPort(Protocol):
    async def publish(self, routing_key: str, payload: dict) -> None: ...


class PlannerPort(Protocol):
    def plan(
        self,
        map_tensor: np.ndarray,
        start_normalized: tuple[float, float],
        goal_normalized: tuple[float, float],
        vessel_class: float,
    ): ...
