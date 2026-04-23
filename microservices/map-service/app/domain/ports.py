"""Ports — abstract interfaces for external dependencies.

Use-cases depend on these Protocols, not on concrete adapters. That's
Dependency Inversion: the inner layer dictates what the outer layer must
provide, not the other way around.
"""

from typing import Protocol


class StoragePort(Protocol):
    async def upload(self, bucket: str, key: str, data: bytes) -> None: ...


class BrokerPort(Protocol):
    async def publish(self, routing_key: str, payload: dict) -> None: ...


class MapGeneratorPort(Protocol):
    def generate(self, height: int, width: int, seed: int) -> dict: ...
