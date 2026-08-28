"""Provider-routed embedding adapter used by durable Narrative RAG."""

from __future__ import annotations

import math
from typing import Any


class EmbeddingProviderUnavailable(RuntimeError):
    """Raised when no usable embedding route or response exists."""


class RoutedEmbeddingProvider:
    """Thin RAG adapter over the Host-owned model runtime."""

    MAX_BATCH_SIZE = 64

    def __init__(self, repository: Any, runtime: Any | None = None):
        self.repository = repository
        self.runtime = runtime
        self.resolved = repository.resolve("embedding")
        self.model_key = (
            f"{self.resolved.get('provider_id', '')}:{self.resolved.get('id', '')}:"
            f"{self.resolved.get('model_id', '')}"
        )

    def bind_runtime(self, runtime: Any) -> None:
        """Bind the Host-owned runtime used for provider invocation."""
        self.runtime = runtime

    def __call__(self, text: str) -> list[float]:
        if not isinstance(text, str) or not text.strip():
            raise EmbeddingProviderUnavailable("embedding input is empty")
        embed = getattr(self.runtime, "embed", None)
        if not callable(embed):
            raise EmbeddingProviderUnavailable(
                "embedding runtime is not attached; provider HTTP cannot bypass the Host runtime"
            )
        try:
            vector = embed(
                text,
                provider_id=str(self.resolved.get("provider_id") or "") or None,
                model_id=str(self.resolved.get("id") or "") or None,
            )
        except Exception as exc:
            raise EmbeddingProviderUnavailable(str(exc)) from exc
        return self._coerce_vector(vector)

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        """Delegate a bounded batch without exposing provider HTTP to RAG."""
        if (
            not isinstance(texts, list)
            or not texts
            or len(texts) > self.MAX_BATCH_SIZE
            or any(not isinstance(text, str) or not text.strip() for text in texts)
        ):
            raise EmbeddingProviderUnavailable(
                f"embedding batch must contain 1..{self.MAX_BATCH_SIZE} non-empty texts"
            )
        embed_many = getattr(self.runtime, "embed_many", None)
        embed = getattr(self.runtime, "embed", None)
        if callable(embed_many):
            try:
                vectors = embed_many(
                    texts,
                    provider_id=str(self.resolved.get("provider_id") or "") or None,
                    model_id=str(self.resolved.get("id") or "") or None,
                )
            except Exception as exc:
                raise EmbeddingProviderUnavailable(str(exc)) from exc
        elif callable(embed):
            try:
                vectors = [
                    embed(
                        text,
                        provider_id=str(self.resolved.get("provider_id") or "") or None,
                        model_id=str(self.resolved.get("id") or "") or None,
                    )
                    for text in texts
                ]
            except Exception as exc:
                raise EmbeddingProviderUnavailable(str(exc)) from exc
        else:
            raise EmbeddingProviderUnavailable(
                "embedding runtime is not attached; provider HTTP cannot bypass the Host runtime"
            )
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise EmbeddingProviderUnavailable("embedding response has an unexpected vector count")
        normalized = [self._coerce_vector(vector) for vector in vectors]
        dimension = len(normalized[0])
        if any(len(vector) != dimension for vector in normalized):
            raise EmbeddingProviderUnavailable("embedding response has inconsistent dimensions")
        return normalized

    @staticmethod
    def _coerce_vector(vector: Any) -> list[float]:
        if not isinstance(vector, list) or not vector:
            raise EmbeddingProviderUnavailable("embedding response has no vector")
        try:
            values = [float(value) for value in vector]
        except (TypeError, ValueError) as exc:
            raise EmbeddingProviderUnavailable("embedding response is not numeric") from exc
        if any(not math.isfinite(value) for value in values):
            raise EmbeddingProviderUnavailable("embedding response is not finite")
        return values
