"""Provider-routed embedding adapter used by durable Narrative RAG."""

from __future__ import annotations

import json
from typing import Any

import httpx


class EmbeddingProviderUnavailable(RuntimeError):
    """Raised when no usable embedding route or response exists."""


class RoutedEmbeddingProvider:
    """Small HTTP adapter; model routing and credentials remain repository-owned."""

    def __init__(self, repository: Any):
        self.repository = repository
        self.resolved = repository.resolve("embedding")
        self.model_key = (
            f"{self.resolved.get('provider_id', '')}:{self.resolved.get('id', '')}:"
            f"{self.resolved.get('model_id', '')}"
        )

    def __call__(self, text: str) -> list[float]:
        if not isinstance(text, str) or not text.strip():
            raise EmbeddingProviderUnavailable("embedding input is empty")
        credential = self.repository.credentials.resolve(self.resolved.get("credential_ref"))
        base_url = str(self.resolved.get("base_url") or "").rstrip("/")
        if not base_url:
            raise EmbeddingProviderUnavailable("embedding provider base URL is missing")
        provider_config = self._object(self.resolved.get("provider_config"))
        timeout = max(1, min(int(provider_config.get("timeout", 60)), 300))
        auth_mode = str(provider_config.get("authHeader") or "bearer").lower()
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if auth_mode in {"api-key", "api_key", "x-api-key"}:
            headers["api-key" if auth_mode == "api-key" else "x-api-key"] = credential
        else:
            headers["Authorization"] = f"Bearer {credential}"
        payload = {"model": self.resolved.get("model_id"), "input": text}
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(f"{base_url}/embeddings", headers=headers, json=payload)
                response.raise_for_status()
                body = response.json()
        except Exception as exc:
            raise EmbeddingProviderUnavailable(str(exc)) from exc
        entries = body.get("data") if isinstance(body, dict) else None
        vector = entries[0].get("embedding") if isinstance(entries, list) and entries else None
        if not isinstance(vector, list) or not vector:
            raise EmbeddingProviderUnavailable("embedding response has no vector")
        try:
            return [float(value) for value in vector]
        except (TypeError, ValueError) as exc:
            raise EmbeddingProviderUnavailable("embedding response is not numeric") from exc

    @staticmethod
    def _object(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        try:
            parsed = json.loads(value or "{}")
        except (TypeError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
