from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.adapters.broker import RabbitBroker
from src.adapters.storage import S3Client
from src.api.routes import router
from src.configs.domain import settings_domain
from src.configs.minio import settings_minio
from src.configs.rabbitmq import settings_rabbitmq
from src.core.generator import PathformerMapGenerator
from src.domain.service import GenerateMapUseCase
from src.unit_of_work import UnitOfWork


@asynccontextmanager
async def lifespan(app: FastAPI):
    broker = RabbitBroker(settings_rabbitmq.url, settings_rabbitmq.exchange)
    await broker.connect()

    storage = S3Client(
        endpoint=settings_minio.URL_MINIO,
        access_key=settings_minio.root_user,
        secret_key=settings_minio.root_password
    )

    await storage.connect()

    generator = PathformerMapGenerator()

    app.state.generate_use_case = GenerateMapUseCase(
        generator=generator,
        bucket=settings_minio.bucket_maps,
        max_current=settings_domain.max_current_global,
        uow_factory=lambda: UnitOfWork(storage, broker)
    )

    try:
        yield
    finally:
        await broker.close()
        await storage.close()


app = FastAPI(title="map-service", lifespan=lifespan)
app.include_router(router)