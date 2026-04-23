"""map-service FastAPI entry point.

Wires concrete adapters (S3Storage, RabbitBroker, PathformerMapGenerator)
to the GenerateMapUseCase and exposes HTTP routes. This is the
composition root — the only place that knows about all the pieces.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .adapters.broker import RabbitBroker
from .adapters.storage import S3Storage
from .api.routes import router
from .core.config import settings
from .core.generator import PathformerMapGenerator
from .domain.service import GenerateMapUseCase


@asynccontextmanager
async def lifespan(app: FastAPI):
    broker = RabbitBroker(settings.rabbitmq_url, settings.rabbitmq_exchange)
    await broker.connect()

    storage = S3Storage(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        use_ssl=settings.minio_use_ssl,
    )

    generator = PathformerMapGenerator()

    app.state.generate_use_case = GenerateMapUseCase(
        generator=generator,
        storage=storage,
        broker=broker,
        bucket=settings.minio_bucket_maps,
        max_current=settings.max_current_global,
    )

    try:
        yield
    finally:
        await broker.close()


app = FastAPI(title="map-service", lifespan=lifespan)
app.include_router(router)
