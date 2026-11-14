"""One shared Gemini client for structured agent output.

The SDK's own retries are disabled. This module retries HTTP 429 only, prints each wait,
and always sends the configured model name. It does not switch to another model.
"""

import sys
import time
from functools import lru_cache
from typing import Protocol

from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from memorybank.config import get_settings

MAX_ATTEMPTS = 3
RETRY_DELAYS_SECONDS = (1, 2)


class LlmError(Exception):
    """Gemini is not configured, or a call failed after the allowed retries."""


class GeminiSettings(Protocol):
    llm_provider: str
    llm_api_key: str
    llm_model: str


def require_gemini(settings: GeminiSettings) -> None:
    """Reject a missing key or a different provider before any network call."""
    if settings.llm_provider != "gemini":
        raise LlmError(f"LLM_PROVIDER must be gemini. It is {settings.llm_provider}.")
    if not settings.llm_api_key:
        raise LlmError("LLM_API_KEY is empty. Set it before calling Gemini.")


def build_client(api_key: str) -> genai.Client:
    """Build a client that does not retry on its own."""
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=30_000,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )


@lru_cache
def get_client() -> genai.Client:
    """Return the process-wide Gemini client. Both agents share this object."""
    settings = get_settings()
    require_gemini(settings)
    return build_client(settings.llm_api_key)


def generate_structured[T: BaseModel](
    schema: type[T],
    system_instruction: str,
    user_text: str,
    *,
    client: genai.Client | None = None,
    model: str | None = None,
    settings: GeminiSettings | None = None,
) -> T:
    """Ask Gemini for one JSON object matching schema. Retry only HTTP 429."""
    if client is None:
        resolved = settings if settings is not None else get_settings()
        require_gemini(resolved)
        model_name = resolved.llm_model
        client = get_client() if settings is None else build_client(resolved.llm_api_key)
    elif not model:
        raise LlmError("A model name is required when the Gemini client is supplied.")
    else:
        model_name = model

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=user_text,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0,
                    response_mime_type="application/json",
                    response_schema=schema,
                ),
            )
        except errors.ClientError as error:
            if error.code != 429:
                raise
            if attempt == MAX_ATTEMPTS:
                raise LlmError(
                    f"Gemini rate limit (429) after {MAX_ATTEMPTS} attempts. "
                    f"Wait and retry. Model stays {model_name}."
                ) from error
            delay = RETRY_DELAYS_SECONDS[attempt - 1]
            print(
                f"Gemini rate limit (429). Waiting {delay} {_seconds(delay)} "
                f"before attempt {attempt + 1} of {MAX_ATTEMPTS}. Model stays {model_name}.",
                file=sys.stderr,
            )
            time.sleep(delay)
            continue
        if response.parsed is None:
            raise LlmError("Gemini returned no structured object.")
        return schema.model_validate(response.parsed)
    raise LlmError(f"Gemini call failed. Model stays {model_name}.")


def _seconds(delay: int) -> str:
    return "second" if delay == 1 else "seconds"
