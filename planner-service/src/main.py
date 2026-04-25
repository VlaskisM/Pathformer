from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.adapters.broker import RabbitBroker
from src.adapters.storage import S3Client
from src.api.routes import router
from src.configs.domain import settings_domain
from src.configs.minio import settings_minio
from src.configs.rabbitmq import settings_rabbitmq
from src.core.planner import ModelPlanner
from src.domain.service import PlanPathUseCase
from src.unit_of_work import UnitOfWork


@asynccontextmanager
async def lifespan(app: FastAPI):
    broker = RabbitBroker(settings_rabbitmq.url, settings_rabbitmq.exchange)
    await broker.connect()

    storage = S3Client(
        endpoint=settings_minio.URL_MINIO,
        access_key=settings_minio.root_user,
        secret_key=settings_minio.root_password,
    )
    await storage.connect()

    planner = ModelPlanner(weights_path=settings_domain.weights_path, device="cpu")

    app.state.storage = storage
    app.state.visuals_bucket = settings_minio.bucket_visuals

    app.state.plan_use_case = PlanPathUseCase(
        planner=planner,
        uow_factory=lambda: UnitOfWork(storage, broker),
        maps_bucket=settings_minio.bucket_maps,
        visuals_bucket=settings_minio.bucket_visuals,
        max_current_global=settings_domain.max_current_global,
    )

    try:
        yield
    finally:
        await broker.close()
        await storage.close()


app = FastAPI(title="planner-service", lifespan=lifespan)
app.include_router(router)
