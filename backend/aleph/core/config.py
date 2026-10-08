from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ALEPH_", env_file=".env", extra="ignore")

    env: str = "dev"  # dev | prod
    database_url: str = "sqlite:///./aleph.db"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = "dev-only-change-me"
    token_minutes: int = 480
    data_dir: str = "./data"

    # FUNES: endpoints compatibles con OpenAI dentro de la red propia (ver .env.example)
    llm_base_url: str = "http://127.0.0.1:4000/v1"
    llm_api_key: str = ""
    llm_model: str = "local"
    embed_base_url: str = "http://127.0.0.1:8081/v1"
    embed_api_key: str = ""
    embed_model: str = "bge-m3"
    qdrant_url: str = "http://127.0.0.1:6333"

    # Claves opcionales de fuentes externas
    virustotal_api_key: str = ""
    otx_api_key: str = ""
    hibp_api_key: str = ""
    x_bearer_token: str = ""
    github_token: str = ""

    faces_enabled: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
