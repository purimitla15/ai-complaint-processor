"""Build the configured LLM client."""

from src.config import Settings
from src.llm.base import LLMClient
from src.llm.gemini_client import GeminiClient
from src.llm.openai_client import OpenAICompatibleClient


def create_llm_client(settings: Settings) -> LLMClient:
    common = dict(
        model=settings.llm_model,
        fallback_model=settings.llm_fallback_model,
        temperature=settings.llm_temperature,
        max_retries=settings.llm_max_retries,
    )

    if settings.llm_provider == "gemini":
        return GeminiClient(api_key=settings.gemini_api_key, **common)
    if settings.llm_provider == "openai":
        return OpenAICompatibleClient(api_key=settings.openai_api_key, **common)
    if settings.llm_provider == "ollama":
        return OpenAICompatibleClient(
            api_key="ollama",  # required by the SDK, ignored by Ollama
            base_url=settings.ollama_base_url,
            provider_name="ollama",
            **common,
        )
    raise ValueError(f"Unsupported provider: {settings.llm_provider}")
