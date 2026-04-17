"""
Central configuration for PascoAsk.
All settings are loaded from environment variables / .env file.
"""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # API Keys
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # Qdrant
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""
    qdrant_collection: str = "pasco_chunks"

    # Storage paths
    sqlite_db_path: Path = Path("./data/pascoask.db")
    raw_data_dir: Path = Path("./data/raw")
    pdf_cache_dir: Path = Path("./data/pdfs")

    # Scraping behaviour
    request_delay_seconds: float = 1.5
    max_concurrent_requests: int = 3

    # Models
    enrichment_model: str = "claude-haiku-4-5-20251001"  # cheap batch enrichment
    answer_model: str = "claude-opus-4-6"                # quality answers

    # Logging
    log_level: str = "INFO"

    def ensure_dirs(self) -> None:
        """Create all required local directories."""
        self.sqlite_db_path.parent.mkdir(parents=True, exist_ok=True)
        self.raw_data_dir.mkdir(parents=True, exist_ok=True)
        self.pdf_cache_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
