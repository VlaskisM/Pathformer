from pydantic_settings import BaseSettings, SettingsConfigDict


class MinioConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="MINIO_",
        extra="ignore",
    )

    host: str
    port: int
    root_user: str
    root_password: str
    bucket_maps: str

    @property
    def URL_MINIO(self) -> str:
        return f"http://{self.host}:{self.port}"


settings_minio = MinioConfig()
