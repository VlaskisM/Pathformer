from urllib.parse import quote

from pydantic_settings import BaseSettings, SettingsConfigDict


class RabbitMQConfig(BaseSettings):

    host: str
    port: int
    user: str
    password: str
    vhost: str
    exchange: str
    

    @property
    def url(self) -> str:
        user = quote(self.user, safe="")
        password = quote(self.password, safe="")
        vhost = quote(self.vhost, safe="")
        return f"amqp://{user}:{password}@{self.host}:{self.port}/{vhost}"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="RABBITMQ_",
        extra="ignore",
    )


settings_rabbitmq = RabbitMQConfig()
