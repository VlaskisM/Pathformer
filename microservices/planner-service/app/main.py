"""planner-service FastAPI entry point — composition root."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .adapters.broker import RabbitBroker
from .adapters.storage import S3Storage
from .api.routes import router
from .core.config import settings
from .core.planner import ModelPlanner
from .domain.service import PlanPathUseCase


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

    planner = ModelPlanner(weights_path=settings.weights_path, device="cpu")

    app.state.storage = storage
    app.state.visuals_bucket = settings.minio_bucket_visuals

    app.state.plan_use_case = PlanPathUseCase(
        storage=storage,
        broker=broker,
        planner=planner,
        maps_bucket=settings.minio_bucket_maps,
        visuals_bucket=settings.minio_bucket_visuals,
        max_current_global=settings.max_current_global,
    )

    try:
        yield
    finally:
        await broker.close()


app = FastAPI(title="planner-service", lifespan=lifespan)
app.include_router(router)
