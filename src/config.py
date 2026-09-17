"""Application settings loaded from environment variables / .env file."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SUPPORTED_PROVIDERS = ("gemini", "openai", "ollama")


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    llm_provider: str
    llm_model: str
    llm_fallback_model: str | None
    llm_temperature: float
    llm_max_retries: int
    gemini_api_key: str | None
    openai_api_key: str | None
    ollama_base_url: str
    data_dir: Path
    output_dir: Path
    max_workers: int
    grounding_check: bool
    log_level: str

    def validate(self) -> None:
        if self.llm_provider not in SUPPORTED_PROVIDERS:
            raise ConfigError(
                f"LLM_PROVIDER must be one of {SUPPORTED_PROVIDERS}, got '{self.llm_provider}'"
            )
        if self.llm_provider == "gemini" and not self.gemini_api_key:
            raise ConfigError("GEMINI_API_KEY is required when LLM_PROVIDER=gemini")
        if self.llm_provider == "openai" and not self.openai_api_key:
            raise ConfigError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
        if self.max_workers < 1:
            raise ConfigError("MAX_WORKERS must be >= 1")


def _resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def load_settings() -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")

    provider = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
    default_models = {
        "gemini": "gemini-flash-latest",
        "openai": "gpt-4o-mini",
        "ollama": "qwen2.5:7b",
    }

    settings = Settings(
        llm_provider=provider,
        llm_model=os.getenv("LLM_MODEL") or default_models.get(provider, ""),
        llm_fallback_model=os.getenv("LLM_FALLBACK_MODEL") or None,
        llm_temperature=float(os.getenv("LLM_TEMPERATURE", "0.2")),
        llm_max_retries=int(os.getenv("LLM_MAX_RETRIES", "4")),
        gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
        data_dir=_resolve_path(os.getenv("DATA_DIR", "data")),
        output_dir=_resolve_path(os.getenv("OUTPUT_DIR", "output")),
        max_workers=int(os.getenv("MAX_WORKERS", "1")),
        grounding_check=os.getenv("EMAIL_GROUNDING_CHECK", "true").strip().lower() in ("1", "true", "yes"),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )
    settings.validate()
    return settings
