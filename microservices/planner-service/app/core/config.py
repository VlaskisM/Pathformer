from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket_maps: str = "maps"
    minio_bucket_visuals: str = "visuals"
    minio_use_ssl: bool = False

    rabbitmq_url: str = "amqp://guest:guest@rabbitmq:5672/"
    rabbitmq_exchange: str = "pathformer"

    weights_path: str = "/app/weights/best.pt"
    max_current_global: float = 3.0


settings = Settings()
