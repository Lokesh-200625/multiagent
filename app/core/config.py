from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All keys / URLs / flags / rates come from environment (.env). No literals in logic."""

    # --- LLM ---
    groq_api_key_1: str | None = None
    groq_api_key_2: str | None = None
    groq_model: str = "openai/gpt-oss-120b"

    # --- Session store ---
    redis_host: str = "localhost"
    redis_port: int = 6379
    session_ttl_s: int = 86_400

    # --- Google Maps (optional; when empty the chain falls back to OSRM) ---
    google_maps_api_key: str | None = None
    google_routes_url: str = "https://routes.googleapis.com/directions/v2:computeRoutes"
    google_timeout_s: float = 6.0

    # --- OSRM (free fallback, no key) ---
    osrm_enabled: bool = True
    osrm_url: str = "https://router.project-osrm.org"
    osrm_timeout_s: float = 5.0

    # --- Nominatim geocoding fallback (registry -> geography -> nominatim) ---
    nominatim_enabled: bool = True
    nominatim_url: str = "https://nominatim.openstreetmap.org"
    nominatim_user_agent: str = "PilgrimAI/1.0"
    nominatim_timeout_s: float = 5.0
    nominatim_min_interval_s: float = 1.1

    # --- Estimates / links (config, not code) ---
    fuel_cost_per_km: float = 6.0
    fallback_transit_link: str = "https://www.redbus.in/"

    # --- Cache TTLs (seconds) ---
    ttl_geocode_s: int = 2_592_000
    ttl_route_s: int = 86_400
    ttl_places_s: int = 86_400
    ttl_negative_s: int = 30

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
