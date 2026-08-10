"""
NovelForge Model Gateway
统一的多Provider LLM网关，支持OpenAI、Anthropic、Gemini等
"""

import base64
import binascii
import json
import time
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, List, Dict, Generator
from enum import Enum
import httpx

logger = logging.getLogger(__name__)


class ProviderType(str, Enum):
    """Provider类型"""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    OPENROUTER = "openrouter"
    CUSTOM = "custom"


@dataclass
class LLMResponse:
    """LLM响应"""
    content: str
    model: str = ""
    tokens_used: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str = ""
    latency_ms: int = 0
    provider: str = ""


@dataclass
class ImageResponse:
    """Binary image returned by an image-capable provider."""

    data: bytes
    mime_type: str = "image/png"
    model: str = ""
    latency_ms: int = 0
    provider: str = ""


@dataclass
class LLMConfig:
    """LLM配置"""
    provider: ProviderType = ProviderType.OPENAI
    model: str = "gpt-4o"
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    temperature: float = 0.8
    max_tokens: int = 8000
    timeout: int = 300
    max_retries: int = 3
    retry_delay: float = 1.0


class BaseProvider(ABC):
    """Provider基类"""
    
    def __init__(self, config: LLMConfig):
        self.config = config
        self.provider_type = config.provider
    
    @abstractmethod
    def chat(self, messages: List[Dict], system: str = "", **kwargs) -> LLMResponse:
        """同步聊天"""
        pass
    
    @abstractmethod
    def chat_stream(self, messages: List[Dict], system: str = "", **kwargs) -> Generator[str, None, None]:
        """流式聊天"""
        pass
    
    def generate_image(self, prompt: str, **kwargs) -> ImageResponse:
        """Generate one image when the provider supports it."""
        raise NotImplementedError("provider does not support image generation")

    def _build_headers(self) -> Dict[str, str]:
        """构建请求头"""
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers
    
    def _handle_error(self, e: Exception, attempt: int) -> bool:
        """处理错误，返回是否重试"""
        if attempt < self.config.max_retries - 1:
            wait_time = self.config.retry_delay * (2 ** attempt)
            logger.warning(f"请求失败，{wait_time}秒后重试: {e}")
            time.sleep(wait_time)
            return True
        return False


class OpenAIProvider(BaseProvider):
    """OpenAI兼容Provider"""
    
    def chat(self, messages: List[Dict], system: str = "", **kwargs) -> LLMResponse:
        headers = self._build_headers()
        
        formatted_messages = []
        if system:
            formatted_messages.append({"role": "system", "content": system})
        formatted_messages.extend(messages)
        
        body = {
            "model": kwargs.get("model", self.config.model),
            "messages": formatted_messages,
            "temperature": kwargs.get("temperature", self.config.temperature),
            "max_tokens": kwargs.get("max_tokens", self.config.max_tokens),
        }
        
        if kwargs.get("json_mode"):
            body["response_format"] = {"type": "json_object"}
        
        start_time = time.time()
        
        for attempt in range(self.config.max_retries):
            try:
                with httpx.Client(timeout=self.config.timeout) as client:
                    response = client.post(
                        f"{self.config.base_url}/chat/completions",
                        headers=headers,
                        json=body
                    )
                    response.raise_for_status()
                    data = response.json()
                
                latency = int((time.time() - start_time) * 1000)
                usage = data.get("usage", {})
                
                return LLMResponse(
                    content=data["choices"][0]["message"]["content"],
                    model=data.get("model", self.config.model),
                    tokens_used=usage.get("total_tokens", 0),
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    finish_reason=data["choices"][0].get("finish_reason", ""),
                    latency_ms=latency,
                    provider="openai"
                )
            except Exception as e:
                if not self._handle_error(e, attempt):
                    raise RuntimeError(f"OpenAI调用失败: {e}")
        raise RuntimeError("OpenAI调用失败: retry loop ended without a response")
    
    def chat_stream(self, messages: List[Dict], system: str = "", **kwargs) -> Generator[str, None, None]:
        headers = self._build_headers()
        
        formatted_messages = []
        if system:
            formatted_messages.append({"role": "system", "content": system})
        formatted_messages.extend(messages)
        
        body = {
            "model": kwargs.get("model", self.config.model),
            "messages": formatted_messages,
            "temperature": kwargs.get("temperature", self.config.temperature),
            "max_tokens": kwargs.get("max_tokens", self.config.max_tokens),
            "stream": True
        }
        
        with httpx.Client(timeout=self.config.timeout) as client:
            with client.stream(
                "POST",
                f"{self.config.base_url}/chat/completions",
                headers=headers,
                json=body
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            delta = chunk["choices"][0].get("delta", {})
                            if "content" in delta:
                                yield delta["content"]
                        except json.JSONDecodeError:
                            continue


    def generate_image(self, prompt: str, **kwargs) -> ImageResponse:
        if not prompt.strip():
            raise ValueError("image prompt must not be empty")
        body = {
            "model": kwargs.get("model", self.config.model),
            "prompt": prompt,
            "n": 1,
            "size": kwargs.get("size", "1024x1024"),
            "response_format": "b64_json",
        }
        for key in ("quality", "style", "background"):
            if kwargs.get(key):
                body[key] = kwargs[key]
        start_time = time.time()
        for attempt in range(self.config.max_retries):
            try:
                with httpx.Client(timeout=self.config.timeout) as client:
                    response = client.post(
                        f"{self.config.base_url}/images/generations",
                        headers=self._build_headers(),
                        json=body,
                    )
                    response.raise_for_status()
                    payload = response.json()
                item = (payload.get("data") or [{}])[0]
                encoded = item.get("b64_json")
                if not isinstance(encoded, str) or not encoded:
                    raise RuntimeError("image provider did not return b64_json")
                try:
                    image = base64.b64decode(encoded, validate=True)
                except (ValueError, binascii.Error) as exc:
                    raise RuntimeError("image provider returned invalid base64") from exc
                if not image:
                    raise RuntimeError("image provider returned an empty image")
                return ImageResponse(
                    data=image,
                    mime_type=str(item.get("mime_type") or "image/png"),
                    model=str(payload.get("model") or self.config.model),
                    latency_ms=int((time.time() - start_time) * 1000),
                    provider="openai",
                )
            except Exception as exc:
                if not self._handle_error(exc, attempt):
                    raise RuntimeError(f"OpenAI image generation failed: {exc}") from exc
        raise RuntimeError("OpenAI image generation failed: retry loop ended without a response")


class AnthropicProvider(BaseProvider):
    """Anthropic Claude Provider"""
    
    def chat(self, messages: List[Dict], system: str = "", **kwargs) -> LLMResponse:
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.config.api_key,
            "anthropic-version": "2023-06-01"
        }
        
        # Anthropic 格式转换
        formatted_messages = []
        for msg in messages:
            formatted_messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })
        
        body = {
            "model": kwargs.get("model", self.config.model),
            "messages": formatted_messages,
            "max_tokens": kwargs.get("max_tokens", self.config.max_tokens),
            "temperature": kwargs.get("temperature", self.config.temperature),
        }
        
        if system:
            body["system"] = system
        
        start_time = time.time()
        
        for attempt in range(self.config.max_retries):
            try:
                with httpx.Client(timeout=self.config.timeout) as client:
                    response = client.post(
                        f"{self.config.base_url}/messages",
                        headers=headers,
                        json=body
                    )
                    response.raise_for_status()
                    data = response.json()
                
                latency = int((time.time() - start_time) * 1000)
                usage = data.get("usage", {})
                
                return LLMResponse(
                    content=data["content"][0]["text"],
                    model=data.get("model", self.config.model),
                    tokens_used=usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
                    prompt_tokens=usage.get("input_tokens", 0),
                    completion_tokens=usage.get("output_tokens", 0),
                    finish_reason=data.get("stop_reason", ""),
                    latency_ms=latency,
                    provider="anthropic"
                )
            except Exception as e:
                if not self._handle_error(e, attempt):
                    raise RuntimeError(f"Anthropic调用失败: {e}")
        raise RuntimeError("Anthropic调用失败: retry loop ended without a response")
    
    def chat_stream(self, messages: List[Dict], system: str = "", **kwargs) -> Generator[str, None, None]:
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.config.api_key,
            "anthropic-version": "2023-06-01"
        }
        
        formatted_messages = []
        for msg in messages:
            formatted_messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })
        
        body = {
            "model": kwargs.get("model", self.config.model),
            "messages": formatted_messages,
            "max_tokens": kwargs.get("max_tokens", self.config.max_tokens),
            "temperature": kwargs.get("temperature", self.config.temperature),
            "stream": True
        }
        
        if system:
            body["system"] = system
        
        with httpx.Client(timeout=self.config.timeout) as client:
            with client.stream(
                "POST",
                f"{self.config.base_url}/messages",
                headers=headers,
                json=body
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                            if data["type"] == "content_block_delta":
                                yield data["delta"]["text"]
                        except (json.JSONDecodeError, KeyError):
                            continue


class GeminiProvider(BaseProvider):
    """Google Gemini Provider"""
    
    def chat(self, messages: List[Dict], system: str = "", **kwargs) -> LLMResponse:
        headers = {"Content-Type": "application/json", "x-goog-api-key": self.config.api_key}
        
        # Gemini 格式转换
        contents = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            contents.append({
                "role": role,
                "parts": [{"text": msg["content"]}]
            })
        
        body = {
            "contents": contents,
            "generationConfig": {
                "temperature": kwargs.get("temperature", self.config.temperature),
                "maxOutputTokens": kwargs.get("max_tokens", self.config.max_tokens),
            }
        }
        
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        
        start_time = time.time()
        model = kwargs.get("model", self.config.model)
        
        for attempt in range(self.config.max_retries):
            try:
                with httpx.Client(timeout=self.config.timeout) as client:
                    response = client.post(
                        f"{self.config.base_url.rstrip('/')}/models/{model}:generateContent",
                        headers=headers,
                        json=body
                    )
                    response.raise_for_status()
                    data = response.json()
                
                latency = int((time.time() - start_time) * 1000)
                usage = data.get("usageMetadata", {})
                
                return LLMResponse(
                    content=data["candidates"][0]["content"]["parts"][0]["text"],
                    model=model,
                    tokens_used=usage.get("totalTokenCount", 0),
                    prompt_tokens=usage.get("promptTokenCount", 0),
                    completion_tokens=usage.get("candidatesTokenCount", 0),
                    finish_reason=data["candidates"][0].get("finishReason", ""),
                    latency_ms=latency,
                    provider="gemini"
                )
            except Exception as e:
                if not self._handle_error(e, attempt):
                    raise RuntimeError(f"Gemini调用失败: {e}")
        raise RuntimeError("Gemini调用失败: retry loop ended without a response")
    
    def chat_stream(self, messages: List[Dict], system: str = "", **kwargs) -> Generator[str, None, None]:
        headers = {"Content-Type": "application/json", "x-goog-api-key": self.config.api_key}
        
        contents = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            contents.append({
                "role": role,
                "parts": [{"text": msg["content"]}]
            })
        
        body = {
            "contents": contents,
            "generationConfig": {
                "temperature": kwargs.get("temperature", self.config.temperature),
                "maxOutputTokens": kwargs.get("max_tokens", self.config.max_tokens),
            }
        }
        
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        
        model = kwargs.get("model", self.config.model)
        
        with httpx.Client(timeout=self.config.timeout) as client:
            with client.stream(
                "POST",
                f"{self.config.base_url.rstrip('/')}/models/{model}:streamGenerateContent?alt=sse",
                headers=headers,
                json=body
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                            text = data["candidates"][0]["content"]["parts"][0]["text"]
                            yield text
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue


class ProviderFactory:
    """Provider工厂"""
    
    _providers = {
        ProviderType.OPENAI: OpenAIProvider,
        ProviderType.ANTHROPIC: AnthropicProvider,
        ProviderType.GEMINI: GeminiProvider,
        ProviderType.OPENROUTER: OpenAIProvider,  # OpenRouter使用OpenAI兼容接口
        ProviderType.CUSTOM: OpenAIProvider,  # 自定义使用OpenAI兼容接口
    }
    
    @classmethod
    def create(cls, config: LLMConfig) -> BaseProvider:
        """创建Provider实例"""
        provider_class = cls._providers.get(config.provider, OpenAIProvider)
        return provider_class(config)
    
    @classmethod
    def register(cls, provider_type: ProviderType, provider_class: type):
        """注册自定义Provider"""
        cls._providers[provider_type] = provider_class


class ModelGateway:
    """模型网关 - 统一的LLM访问入口"""
    
    def __init__(self):
        self._providers: Dict[str, BaseProvider] = {}
        self._configs: Dict[str, LLMConfig] = {}
        self._usage_stats: Dict[str, Dict] = {}
    
    def register_provider(self, name: str, config: LLMConfig):
        """注册Provider"""
        self._configs[name] = config
        self._providers[name] = ProviderFactory.create(config)
        self._usage_stats[name] = {
            "total_calls": 0,
            "total_tokens": 0,
            "total_latency_ms": 0,
            "errors": 0
        }
        logger.info(f"注册Provider: {name} ({config.provider.value})")
    
    def get_provider(self, name: str = "default") -> BaseProvider:
        """获取Provider"""
        if name not in self._providers:
            raise ValueError(f"Provider不存在: {name}")
        return self._providers[name]
    
    def chat(self, provider_name: str, messages: List[Dict], 
             system: str = "", **kwargs) -> LLMResponse:
        """调用聊天"""
        provider = self.get_provider(provider_name)
        
        try:
            response = provider.chat(messages, system, **kwargs)
            
            # 更新统计
            stats = self._usage_stats[provider_name]
            stats["total_calls"] += 1
            stats["total_tokens"] += response.tokens_used
            stats["total_latency_ms"] += response.latency_ms
            
            return response
        except Exception:
            self._usage_stats[provider_name]["errors"] += 1
            raise
    
    def chat_stream(self, provider_name: str, messages: List[Dict],
                    system: str = "", **kwargs) -> Generator[str, None, None]:
        """流式聊天"""
        provider = self.get_provider(provider_name)
        return provider.chat_stream(messages, system, **kwargs)
    
    def chat_json(self, provider_name: str, messages: List[Dict],
                  system: str = "", **kwargs) -> Dict:
        """聊天并返回JSON"""
        kwargs["json_mode"] = True
        response = self.chat(provider_name, messages, system, **kwargs)
        
        try:
            content = response.content.strip()
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            return json.loads(content)
        except json.JSONDecodeError:
            try:
                start = content.index("{")
                end = content.rindex("}") + 1
                return json.loads(content[start:end])
            except (ValueError, json.JSONDecodeError):
                return {"raw": content, "error": "JSON解析失败"}
    
    def generate_image(self, provider_name: str, prompt: str, **kwargs) -> ImageResponse:
        """Generate an image through an image-capable provider."""
        provider = self.get_provider(provider_name)
        try:
            response = provider.generate_image(prompt, **kwargs)
            stats = self._usage_stats[provider_name]
            stats["total_calls"] += 1
            stats["total_latency_ms"] += response.latency_ms
            return response
        except Exception:
            self._usage_stats[provider_name]["errors"] += 1
            raise

    def get_usage_stats(self, provider_name: Optional[str] = None) -> Dict:
        """获取使用统计"""
        if provider_name:
            return self._usage_stats.get(provider_name, {})
        return self._usage_stats
    
    def list_providers(self) -> List[str]:
        """列出所有Provider"""
        return list(self._providers.keys())


# 全局网关实例
_gateway: Optional[ModelGateway] = None


def get_gateway() -> ModelGateway:
    """获取全局网关"""
    global _gateway
    if _gateway is None:
        _gateway = ModelGateway()
    return _gateway


def init_gateway_from_config(config) -> ModelGateway:
    """从配置初始化网关"""
    gateway = get_gateway()
    
    # 注册主模型
    primary_config = config.get("llm", "primary", default={})
    if primary_config:
        gateway.register_provider("primary", LLMConfig(
            provider=ProviderType(primary_config.get("provider", "openai")),
            model=primary_config.get("model", "gpt-4o"),
            base_url=primary_config.get("base_url", "https://api.openai.com/v1"),
            api_key=primary_config.get("api_key", ""),
            temperature=primary_config.get("temperature", 0.8),
            max_tokens=primary_config.get("max_tokens", 8000),
        ))
    
    # 注册审查模型
    review_config = config.get("llm", "review", default={})
    if review_config:
        gateway.register_provider("review", LLMConfig(
            provider=ProviderType(review_config.get("provider", "openai")),
            model=review_config.get("model", "gpt-4o"),
            base_url=review_config.get("base_url", "https://api.openai.com/v1"),
            api_key=review_config.get("api_key", ""),
            temperature=review_config.get("temperature", 0.3),
            max_tokens=review_config.get("max_tokens", 4000),
        ))
    
    return gateway
