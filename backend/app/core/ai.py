import httpx
from openai import AsyncOpenAI

from app.core.config import settings

GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
GROQ_OPENAI_BASE_URL = "https://api.groq.com/openai/v1"


class AIClientConfigurationError(RuntimeError):
    pass


def create_ai_client() -> AsyncOpenAI:
    if settings.ai_provider == "gemini":
        api_key = settings.gemini_api_key
        base_url = GEMINI_OPENAI_BASE_URL
        key_name = "GEMINI_API_KEY"
    elif settings.ai_provider == "groq":
        api_key = settings.groq_api_key
        base_url = GROQ_OPENAI_BASE_URL
        key_name = "GROQ_API_KEY"
    else:
        raise AIClientConfigurationError(
            f"AI provider {settings.ai_provider!r} does not support the hosted LLM client"
        )

    if api_key is None:
        raise AIClientConfigurationError(
            f"{key_name} is not configured. Add it to backend/.env and restart the API."
        )
    return AsyncOpenAI(
        api_key=api_key.get_secret_value(),
        base_url=base_url,
        timeout=httpx.Timeout(90.0),
    )
