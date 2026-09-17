"""Google Gemini provider using native structured output (response_schema)."""

from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from src.llm.base import LLMClient, LLMError, TransientLLMError

RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


class GeminiClient(LLMClient):
    provider_name = "gemini"

    def __init__(self, api_key: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._client = genai.Client(api_key=api_key)

    def _complete_json(
        self, model: str, system_prompt: str, user_prompt: str, schema: type[BaseModel]
    ) -> str:
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=self.temperature,
            response_mime_type="application/json",
            response_schema=schema,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        try:
            response = self._client.models.generate_content(
                model=model, contents=user_prompt, config=config
            )
        except errors.APIError as exc:
            if exc.code in RETRYABLE_STATUS_CODES:
                raise TransientLLMError(f"Gemini HTTP {exc.code}: {exc.status}") from exc
            raise LLMError(f"Gemini HTTP {exc.code}: {exc.message}") from exc
        except OSError as exc:  # network / DNS / timeout
            raise TransientLLMError(f"Gemini network error: {exc}") from exc

        return response.text or ""
