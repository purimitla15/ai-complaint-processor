"""OpenAI-compatible provider. Used for both OpenAI and local Ollama.

Ollama exposes an OpenAI-compatible API at http://localhost:11434/v1 and
supports JSON-schema constrained output via `response_format`.
"""

import openai
from pydantic import BaseModel

from src.llm.base import LLMClient, LLMError, TransientLLMError


class OpenAICompatibleClient(LLMClient):
    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        provider_name: str = "openai",
        timeout_seconds: float = 300.0,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.provider_name = provider_name
        self._client = openai.OpenAI(
            api_key=api_key, base_url=base_url, timeout=timeout_seconds, max_retries=0
        )

    def _complete_json(
        self, model: str, system_prompt: str, user_prompt: str, schema: type[BaseModel]
    ) -> str:
        try:
            response = self._client.chat.completions.create(
                model=model,
                temperature=self.temperature,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema.__name__,
                        "schema": schema.model_json_schema(),
                    },
                },
            )
        except (
            openai.RateLimitError,
            openai.APITimeoutError,
            openai.APIConnectionError,
            openai.InternalServerError,
        ) as exc:
            raise TransientLLMError(f"{self.provider_name}: {type(exc).__name__}: {exc}") from exc
        except openai.NotFoundError as exc:
            hint = f" (run `ollama pull {model}`)" if self.provider_name == "ollama" else ""
            raise LLMError(f"{self.provider_name}: model '{model}' not found{hint}") from exc
        except openai.APIError as exc:
            raise LLMError(f"{self.provider_name}: {type(exc).__name__}: {exc}") from exc

        return response.choices[0].message.content or ""
