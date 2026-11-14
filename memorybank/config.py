"""Typed application settings, loaded from the environment and a local .env file.

The embedding dimension is defined here and is the single source of truth for the
Atlas Vector Search index (see scripts/setup_indexes.py).
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # MongoDB Atlas (hackathon Sandbox cluster)
    mongodb_uri: str = Field(min_length=1)
    mongodb_db: str = "memorybank"

    # Voyage AI embeddings
    # Optional at load time so the Mongo connection check works before a Voyage key exists.
    # Embedding code must raise a clear error if this is empty.
    voyage_api_key: str = ""
    # voyage-4 has the 200M free-token allowance on the hackathon account (voyage-3.5 does not).
    voyage_model: str = "voyage-4"
    embedding_dim: int = 1024

    # LLM for structured routing actions
    llm_provider: str = "anthropic"
    llm_api_key: str = ""
    llm_model: str = ""

    # Services
    ticket_service_url: str = "http://localhost:8001"
    api_port: int = 8000


@lru_cache
def get_settings() -> Settings:
    """Load settings once. Fails fast if a required value is missing."""
    return Settings()
