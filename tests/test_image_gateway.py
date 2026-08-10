"""Contract tests for the image-capable provider boundary."""

from __future__ import annotations

import base64
from unittest.mock import Mock, patch

from src.llm.gateway import ImageResponse, LLMConfig, OpenAIProvider, ProviderType
from src.llm.model_runtime import PersistentMultiModelManager


def test_openai_image_provider_decodes_binary_response_and_sends_options():
    response = Mock()
    response.raise_for_status = Mock()
    response.json.return_value = {
        "model": "gpt-image-1",
        "data": [{"b64_json": base64.b64encode(b"png-bytes").decode(), "mime_type": "image/png"}],
    }
    client = Mock()
    client.post.return_value = response
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)

    with patch("src.llm.gateway.httpx.Client", return_value=client):
        result = OpenAIProvider(LLMConfig(provider=ProviderType.OPENAI, api_key="secret", max_retries=1)).generate_image(
            "a quiet literary cover",
            size="1024x1536",
            quality="hd",
            style="natural",
        )

    assert result.data == b"png-bytes"
    assert result.mime_type == "image/png"
    assert result.model == "gpt-image-1"
    request = client.post.call_args
    assert request.args[0].endswith("/images/generations")
    assert request.kwargs["json"]["prompt"] == "a quiet literary cover"
    assert request.kwargs["json"]["size"] == "1024x1536"
    assert request.kwargs["json"]["quality"] == "hd"
    assert request.kwargs["json"]["style"] == "natural"


def test_legacy_model_manager_forwards_image_generation_to_durable_runtime():
    runtime = Mock()
    expected = ImageResponse(data=b"bytes", model="image-model")
    runtime.generate_image.return_value = expected
    manager = PersistentMultiModelManager(runtime)

    result = manager.generate_image("cover prompt", size="1024x1536", quality="hd", style="natural")

    assert result is expected
    runtime.generate_image.assert_called_once_with(
        "cover prompt", size="1024x1536", quality="hd", style="natural"
    )
