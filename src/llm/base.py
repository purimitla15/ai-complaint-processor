"""Provider-agnostic LLM client with retries, model fallback and schema validation."""

import re
import time
from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel, ValidationError
from tenacity import (
    RetryError,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.logger import get_logger

logger = get_logger(__name__)

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class LLMError(Exception):
    """Non-recoverable LLM failure (bad credentials, exhausted retries, invalid output)."""


class TransientLLMError(Exception):
    """Recoverable failure: rate limit, overload, timeout, network, or malformed JSON."""


_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


class LLMClient(ABC):
    provider_name: str = "base"

    def __init__(
        self,
        model: str,
        fallback_model: str | None = None,
        temperature: float = 0.2,
        max_retries: int = 4,
    ) -> None:
        self.model = model
        self.fallback_model = fallback_model if fallback_model != model else None
        self.temperature = temperature
        self.max_retries = max_retries

    @abstractmethod
    def _complete_json(
        self, model: str, system_prompt: str, user_prompt: str, schema: type[BaseModel]
    ) -> str:
        """Call the provider and return raw JSON text for `schema`.

        Implementations must raise TransientLLMError for retryable problems and
        LLMError for permanent ones.
        """

    def generate_structured(
        self, system_prompt: str, user_prompt: str, schema: type[SchemaT], task_name: str
    ) -> SchemaT:
        """Generate an output validated against `schema`, trying the fallback model if needed."""
        models = [self.model] + ([self.fallback_model] if self.fallback_model else [])
        last_error: Exception | None = None

        for model in models:
            try:
                return self._generate_with_retries(model, system_prompt, user_prompt, schema, task_name)
            except TransientLLMError as exc:
                last_error = exc
                if model != models[-1]:
                    logger.warning(
                        "[%s] model '%s' unavailable after %d attempts (%s); falling back to '%s'",
                        task_name, model, self.max_retries, exc, models[-1],
                    )

        raise LLMError(f"{task_name} failed on all models: {last_error}") from last_error

    def _generate_with_retries(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        schema: type[SchemaT],
        task_name: str,
    ) -> SchemaT:
        retrying = Retrying(
            stop=stop_after_attempt(self.max_retries),
            wait=wait_exponential(multiplier=2, min=2, max=30),
            retry=retry_if_exception_type(TransientLLMError),
            before_sleep=lambda state: logger.warning(
                "[%s] attempt %d on '%s' failed: %s - retrying",
                task_name, state.attempt_number, model, state.outcome.exception(),
            ),
        )
        try:
            for attempt in retrying:
                with attempt:
                    started = time.perf_counter()
                    raw = self._complete_json(model, system_prompt, user_prompt, schema)
                    result = self._parse(raw, schema)
                    logger.info(
                        "[%s] completed with '%s' in %.1fs", task_name, model, time.perf_counter() - started
                    )
                    return result
        except RetryError as exc:
            raise exc.last_attempt.exception() from exc
        raise LLMError(f"{task_name}: no attempts were made")  # pragma: no cover

    @staticmethod
    def _parse(raw: str, schema: type[SchemaT]) -> SchemaT:
        """Validate raw model output against the schema; malformed output is retryable."""
        if not raw or not raw.strip():
            raise TransientLLMError("empty response from model")
        cleaned = _CODE_FENCE.sub("", raw.strip())
        try:
            return schema.model_validate_json(cleaned)
        except ValidationError as exc:
            raise TransientLLMError(
                f"output did not match {schema.__name__} schema ({exc.error_count()} errors)"
            ) from exc
