from abc import ABC, abstractmethod

from src.adapters.broker import RabbitBrokerInterface
from src.adapters.storage import S3ClientInterface


class UnitOfWorkInterface(ABC):
    _storage: S3ClientInterface
    _broker: RabbitBrokerInterface

    @abstractmethod
    async def __aenter__(self): ...

    @abstractmethod
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None: ...

    @abstractmethod
    async def upload(self, bucket: str, key: str, data: bytes) -> None: ...

    @abstractmethod
    async def publish(self, routing_key: str, payload: dict) -> None: ...

    @abstractmethod
    async def commit(self) -> None: ...

    @abstractmethod
    async def rollback(self) -> None: ...


class UnitOfWork(UnitOfWorkInterface):

    def __init__(self, storage: S3ClientInterface, broker: RabbitBrokerInterface) -> None:
        self._storage = storage
        self._broker = broker
        self._pending_events: list[tuple[str, dict]] = []
        self._pending_uploads: list[tuple[str, str, bytes]] = []
        self._committed = False

    async def __aenter__(self):
        self._pending_events = []
        self._pending_uploads = []
        self._committed = False
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is not None:
            await self.rollback()
            return
        try:
            await self.commit()
        except Exception:
            await self.rollback()
            raise

            
    async def upload(self, bucket: str, key: str, data: bytes) -> None:
        if self._committed:
            raise RuntimeError("Unit of work already committed")

        self._pending_uploads.append((bucket, key, data))

    async def publish(self, routing_key: str, payload: dict) -> None:
        if self._committed:
            raise RuntimeError("Unit of work already committed")

        self._pending_events.append((routing_key, payload))

    async def commit(self) -> None:
        if self._committed:
            raise RuntimeError("Unit of work already committed")

        try:
            for bucket, key, data in self._pending_uploads:
                await self._storage.upload(bucket, key, data)
        except Exception:
            await self._storage.delete(bucket, key)
            raise RuntimeError("Failed to upload file")

        try:
            for routing_key, payload in self._pending_events:
                await self._broker.publish(routing_key, payload)
        except Exception:
            raise RuntimeError("Failed to upload publish event")
        

        self._pending_uploads.clear()
        self._pending_events.clear()
        self._committed = True

    async def rollback(self) -> None:
        if self._committed:
            raise RuntimeError("Unit of work already committed")
            
        self._pending_events.clear()
        self._pending_uploads.clear()
        self._committed = False