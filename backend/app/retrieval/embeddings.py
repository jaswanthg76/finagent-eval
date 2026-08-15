import asyncio
from collections.abc import Sequence
from functools import cache
from hashlib import sha256
from typing import Any, Protocol

from fastembed import TextEmbedding
from openai import AsyncOpenAI

from app.core.config import settings


class EmbeddingConfigurationError(ValueError):
    pass


class EmbeddingGenerationError(RuntimeError):
    pass


class EmbeddingClient(Protocol):
    async def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


def content_hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _validate_vectors(vectors: list[list[float]], expected_count: int) -> None:
    if len(vectors) != expected_count:
        raise EmbeddingGenerationError("Embedding model returned an unexpected vector count")
    if any(len(vector) != settings.embedding_dimensions for vector in vectors):
        raise EmbeddingGenerationError("Embedding model returned unexpected dimensions")


@cache
def _load_local_model(model_name: str) -> TextEmbedding:
    return TextEmbedding(model_name=model_name)


def _embed_locally(model: Any, texts: Sequence[str]) -> list[list[float]]:
    return [vector.tolist() for vector in model.embed(list(texts))]


class LocalEmbeddingClient:
    def __init__(self, model: Any | None = None) -> None:
        self._model = model

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        try:
            model = self._model or await asyncio.to_thread(
                _load_local_model, settings.embedding_model
            )
            vectors = await asyncio.to_thread(_embed_locally, model, texts)
        except EmbeddingGenerationError:
            raise
        except Exception as error:
            raise EmbeddingGenerationError("Local embedding generation failed") from error
        _validate_vectors(vectors, len(texts))
        return vectors


class OpenAIEmbeddingClient:
    def __init__(self, client: Any | None = None, model_name: str | None = None) -> None:
        if client is None:
            if settings.openai_api_key is None:
                raise EmbeddingConfigurationError(
                    "OPENAI_API_KEY must be configured to generate embeddings"
                )
            client = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())
        self._client = client
        self._model_name = model_name or settings.embedding_model

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        response = await self._client.embeddings.create(
            model=self._model_name,
            input=texts,
            dimensions=settings.embedding_dimensions,
            encoding_format="float",
        )
        vectors = [item.embedding for item in sorted(response.data, key=lambda item: item.index)]
        _validate_vectors(vectors, len(texts))
        return vectors


def create_embedding_client() -> EmbeddingClient:
    if settings.embedding_provider == "local":
        return LocalEmbeddingClient()
    if settings.embedding_provider == "openai":
        return OpenAIEmbeddingClient()
    raise EmbeddingConfigurationError(
        f"Unsupported embedding provider: {settings.embedding_provider}"
    )
