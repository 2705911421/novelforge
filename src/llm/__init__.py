"""LLM模块初始化"""

from .client import LLMClient, MultiModelManager, LLMResponse
from .gateway import (
    ModelGateway, LLMConfig, ProviderType, ProviderFactory,
    BaseProvider, OpenAIProvider, AnthropicProvider, GeminiProvider,
    get_gateway, init_gateway_from_config
)
from .router import (
    ModelRouter, AgentRouter, AgentClient, AgentRole,
    get_agent_router, init_agent_router_from_config
)

__all__ = [
    # Legacy
    "LLMClient", "MultiModelManager", "LLMResponse",
    # Gateway
    "ModelGateway", "LLMConfig", "ProviderType", "ProviderFactory",
    "BaseProvider", "OpenAIProvider", "AnthropicProvider", "GeminiProvider",
    "get_gateway", "init_gateway_from_config",
    # Router
    "ModelRouter", "AgentRouter", "AgentClient", "AgentRole",
    "get_agent_router", "init_agent_router_from_config",
]
