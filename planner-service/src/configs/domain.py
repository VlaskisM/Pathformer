from pydantic_settings import BaseSettings, SettingsConfigDict


class DomainConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    max_current_global: float = 3.0
    weights_path: str = "/app/weights/best.pt"


settings_domain = DomainConfig()
