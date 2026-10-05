from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    groq_api_key_1: str | None = None
    groq_api_key_2: str | None = None
    groq_model: str = "openai/gpt-oss-120b"

    redis_host: str = "localhost"
    redis_port: int = 6379

    ors_api_key: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()