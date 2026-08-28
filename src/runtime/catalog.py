"""Safe transport for signed Runtime catalogs.

Fetching is kept separate from :class:`ManifestCatalog`: transport only
retrieves bounded JSON, while the catalog verifier remains the sole authority
that can import manifests into the persistent Registry.  No fetched payload
is executed as a command.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping
from urllib.parse import urlparse
from urllib.request import Request

from .errors import RuntimeUnavailable
from .registry import (
    ArtifactDownloader,
    ManifestCatalog,
    RuntimeManifest,
    RuntimeRegistry,
    _open_validated_url,
)


class RuntimeCatalogClient:
    """Fetch a complete catalog over an explicitly allowed HTTPS boundary."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 15.0,
        max_bytes: int = 2 * 1024 * 1024,
        opener: Callable[..., Any] | None = None,
        allow_loopback_http: bool = False,
        allow_private_network: bool = False,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.timeout_seconds = float(timeout_seconds)
        self.max_bytes = int(max_bytes)
        self.opener = opener or self._open_url
        self.allow_loopback_http = bool(allow_loopback_http)
        self.allow_private_network = bool(allow_private_network)

    def fetch_document(self, url: str) -> Mapping[str, Any]:
        normalized_url = str(url or "").strip()
        self._validate_url(normalized_url)
        request = Request(
            normalized_url,
            headers={
                "Accept": "application/json",
                "User-Agent": "NovelForge-RuntimeCatalog/1",
            },
            method="GET",
        )
        try:
            response = self.opener(request, timeout=self.timeout_seconds)
        except Exception as exc:
            raise RuntimeUnavailable(
                f"runtime catalog fetch failed: {exc}",
                details={"url": normalized_url},
            ) from exc
        try:
            final_url = getattr(response, "geturl", lambda: normalized_url)()
            self._validate_url(str(final_url or normalized_url))
            status = getattr(response, "status", None)
            if status is None:
                status = getattr(response, "code", 200)
            if isinstance(status, int) and status >= 400:
                raise RuntimeUnavailable(
                    f"runtime catalog returned HTTP {status}",
                    details={"url": normalized_url, "status": status},
                )
            headers = getattr(response, "headers", None)
            content_length = headers.get("Content-Length") if headers is not None else None
            if content_length is not None:
                try:
                    if int(content_length) > self.max_bytes:
                        raise RuntimeUnavailable("runtime catalog exceeds the maximum size")
                except ValueError as exc:
                    raise RuntimeUnavailable("runtime catalog Content-Length is invalid") from exc
            raw = self._read_bounded(response)
        except RuntimeUnavailable:
            raise
        except Exception as exc:
            raise RuntimeUnavailable(
                f"runtime catalog response could not be read: {exc}",
                details={"url": normalized_url},
            ) from exc
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        if not isinstance(raw, (bytes, bytearray)):
            raise RuntimeUnavailable("runtime catalog response was not bytes")
        if len(raw) > self.max_bytes:
            raise RuntimeUnavailable("runtime catalog exceeds the maximum size")
        try:
            document = json.loads(bytes(raw).decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeUnavailable("runtime catalog is not valid UTF-8 JSON") from exc
        if not isinstance(document, Mapping):
            raise RuntimeUnavailable("runtime catalog root must be an object")
        return document

    def _read_bounded(self, response: Any) -> bytes:
        """Read a possibly short-reading response without exceeding the cap."""
        chunks: list[bytes] = []
        total = 0
        while total <= self.max_bytes:
            chunk = response.read(min(64 * 1024, self.max_bytes - total + 1))
            if not chunk:
                break
            if not isinstance(chunk, (bytes, bytearray)):
                raise RuntimeUnavailable("runtime catalog response was not bytes")
            total += len(chunk)
            if total > self.max_bytes:
                raise RuntimeUnavailable("runtime catalog exceeds the maximum size")
            chunks.append(bytes(chunk))
        return b"".join(chunks)

    def fetch_and_import(
        self,
        url: str,
        catalog: ManifestCatalog,
        registry: RuntimeRegistry,
    ) -> tuple[RuntimeManifest, ...]:
        """Fetch bytes, then verify the full signature before Registry writes."""
        return catalog.import_into(registry, self.fetch_document(url))

    def _validate_url(self, url: str) -> None:
        try:
            parsed = urlparse(url)
        except ValueError as exc:
            raise RuntimeUnavailable("runtime catalog URL is malformed") from exc
        if parsed.username or parsed.password:
            raise RuntimeUnavailable("runtime catalog URL must not contain userinfo")
        if not parsed.netloc:
            raise RuntimeUnavailable("runtime catalog URL must include a host")
        if ArtifactDownloader.url_allowed(
            url,
            allow_loopback_http=self.allow_loopback_http,
            allow_private_network=self.allow_private_network,
        ):
            return
        raise RuntimeUnavailable(
            "runtime catalog transport requires HTTPS and a non-private host"
        )

    def _open_url(self, request: Request, *, timeout: float) -> Any:
        """Use the Host transport policy for every redirect hop."""
        return _open_validated_url(request, timeout=timeout, validator=self._validate_url)
