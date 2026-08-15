from hashlib import sha256
from typing import Any

from openai import AsyncOpenAI

from app.core.config import settings


class EmbeddingConfigurationError(ValueError):
    pass


def content_hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


class OpenAIEmbeddingClient:
    def __init__(self, client: Any | None = None) -> None:
        if client is None:
            if settings.openai_api_key is None:
                raise EmbeddingConfigurationError(
                    "OPENAI_API_KEY must be configured to generate embeddings"
                )
            client = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())
        self._client = client

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        response = await self._client.embeddings.create(
            model=settings.embedding_model,
            input=texts,
            dimensions=settings.embedding_dimensions,
            encoding_format="float",
        )
        vectors = [item.embedding for item in sorted(response.data, key=lambda item: item.index)]
        if len(vectors) != len(texts):
            raise RuntimeError("OpenAI returned an unexpected number of embeddings")
        if any(len(vector) != settings.embedding_dimensions for vector in vectors):
            raise RuntimeError("OpenAI returned an embedding with unexpected dimensions")
        return vectors

