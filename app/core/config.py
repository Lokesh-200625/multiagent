from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    groq_api_key_1: str | None = None
    groq_api_key_2: str | None = None
    groq_model: str = "openai/gpt-oss-120b"

    redis_host: str = "localhost"
    redis_port: int = 6379

    # --- Travel providers ---
    ors_enabled: bool = True
    ors_url: str = "https://api.openrouteservice.org/v2/directions/driving-car"
    ors_api_key: str | None = None
    ors_timeout_s: float = 5.0
    ors_daily_quota: int = 1500

    osrm_enabled: bool = True
    osrm_url: str = "https://router.project-osrm.org"
    osrm_timeout_s: float = 5.0

    nominatim_enabled: bool = True
    nominatim_url: str = "https://nominatim.openstreetmap.org"
    nominatim_user_agent: str = "PilgrimAI/1.0"
    nominatim_timeout_s: float = 5.0
    nominatim_min_interval_s: float = 1.1

    # --- Travel cache TTLs (seconds) ---
    ttl_geocode_s: int = 2_592_000
    ttl_route_s: int = 86_400
    ttl_negative_s: int = 30

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()