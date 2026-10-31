"""Replaceable embeddings. Demo hashing is deterministic, NOT semantic model quality."""

import asyncio
import hashlib
import math
import re
from typing import Protocol

from openai import AsyncOpenAI, OpenAIError

from app.config import Settings
from app.provider import ProviderUnavailable
from app.telemetry import emit, record_usage


class Embedder(Protocol):
    identity: str
    dimensions: int

    def embed(self, texts: list[str], timeout: float = 6) -> list[list[float]]: ...


def validate_vectors(vectors, count: int, dimensions: int) -> list[list[float]]:
    if len(vectors) != count:
        raise ProviderUnavailable()
    result = []
    for vector in vectors:
        if len(vector) != dimensions or not all(math.isfinite(v) for v in vector):
            raise ProviderUnavailable()
        norm = math.sqrt(sum(v * v for v in vector))
        if norm <= 0:
            raise ProviderUnavailable()
        result.append([float(v) / norm for v in vector])
    return result


class DemoEmbedder:
    def __init__(self, dimensions: int = 256):
        self.dimensions = dimensions
        self.identity = f"demo-hash-v1:{dimensions}"

    def embed(self, texts: list[str], timeout: float = 6) -> list[list[float]]:
        record_usage("embedding_demo", None, None)
        vectors = []
        for text in texts:
            vector = [0.0] * self.dimensions
            for word in re.findall(r"[a-z0-9]+", text.lower()):
                index = int.from_bytes(hashlib.sha256(word.encode()).digest()[:4]) % self.dimensions
                vector[index] += 1
            vectors.append(vector)
        return validate_vectors(vectors, len(texts), self.dimensions)


class OpenAIEmbedder:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.dimensions = settings.embedding_dimensions
        self.identity = f"openai:{settings.embedding_model}:{self.dimensions}"

    def embed(self, texts: list[str], timeout: float = 6) -> list[list[float]]:
        async def call():
            key = self.settings.openai_api_key
            if not key or not key.get_secret_value():
                raise ProviderUnavailable()
            async with AsyncOpenAI(
                api_key=key.get_secret_value(), timeout=timeout, max_retries=0
            ) as client:
                emit("provider.attempt", provider="embedding_openai")
                response = await client.embeddings.create(
                    model=self.settings.embedding_model,
                    dimensions=self.dimensions,
                    input=texts,
                    encoding_format="float",
                )
                record_usage("embedding_openai", getattr(response, "usage", None), self.settings)
                rows = sorted(response.data, key=lambda row: row.index)
                if [row.index for row in rows] != list(range(len(texts))):
                    raise ProviderUnavailable()
                return validate_vectors(
                    [row.embedding for row in rows], len(texts), self.dimensions
                )

        async def bounded():
            return await asyncio.wait_for(call(), timeout)

        try:
            return asyncio.run(bounded())
        except OpenAIError, TimeoutError:
            raise ProviderUnavailable() from None


def make_embedder(settings: Settings) -> Embedder:
    if settings.embedding_provider == "demo":
        return DemoEmbedder(settings.embedding_dimensions)
    return OpenAIEmbedder(settings)
