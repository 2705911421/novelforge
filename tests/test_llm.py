"""
NovelForge LLM模块测试
"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm.gateway import (
    ModelGateway, LLMConfig, ProviderType, LLMResponse,
    OpenAIProvider, AnthropicProvider, GeminiProvider,
    ProviderFactory
)
from src.llm.router import (
    ModelRouter, AgentRouter, AgentClient, AgentRole
)


# ========== LLMConfig 测试 ==========

class TestLLMConfig:
    """LLM配置测试"""
    
    def test_default_config(self):
        """测试默认配置"""
        config = LLMConfig()
        assert config.provider == ProviderType.OPENAI
        assert config.model == "gpt-4o"
        assert config.temperature == 0.8
        assert config.max_tokens == 8000
        assert config.max_retries == 3
    
    def test_custom_config(self):
        """测试自定义配置"""
        config = LLMConfig(
            provider=ProviderType.ANTHROPIC,
            model="claude-3-opus",
            temperature=0.5,
            max_tokens=4000
        )
        assert config.provider == ProviderType.ANTHROPIC
        assert config.model == "claude-3-opus"
        assert config.temperature == 0.5


# ========== ProviderFactory 测试 ==========

class TestProviderFactory:
    """Provider工厂测试"""
    
    def test_create_openai_provider(self):
        """测试创建OpenAI Provider"""
        config = LLMConfig(provider=ProviderType.OPENAI)
        provider = ProviderFactory.create(config)
        assert isinstance(provider, OpenAIProvider)
    
    def test_create_anthropic_provider(self):
        """测试创建Anthropic Provider"""
        config = LLMConfig(provider=ProviderType.ANTHROPIC)
        provider = ProviderFactory.create(config)
        assert isinstance(provider, AnthropicProvider)
    
    def test_create_gemini_provider(self):
        """测试创建Gemini Provider"""
        config = LLMConfig(provider=ProviderType.GEMINI)
        provider = ProviderFactory.create(config)
        assert isinstance(provider, GeminiProvider)
    
    def test_create_openrouter_provider(self):
        """测试创建OpenRouter Provider (使用OpenAI兼容)"""
        config = LLMConfig(provider=ProviderType.OPENROUTER)
        provider = ProviderFactory.create(config)
        assert isinstance(provider, OpenAIProvider)


# ========== ModelGateway 测试 ==========

class TestModelGateway:
    """模型网关测试"""
    
    def setup_method(self):
        """测试前准备"""
        self.gateway = ModelGateway()
    
    def test_register_provider(self):
        """测试注册Provider"""
        config = LLMConfig(
            provider=ProviderType.OPENAI,
            api_key="test-key"
        )
        self.gateway.register_provider("test", config)
        assert "test" in self.gateway.list_providers()
    
    def test_get_provider(self):
        """测试获取Provider"""
        config = LLMConfig(provider=ProviderType.OPENAI)
        self.gateway.register_provider("test", config)
        
        provider = self.gateway.get_provider("test")
        assert provider is not None
    
    def test_get_nonexistent_provider(self):
        """测试获取不存在的Provider"""
        with pytest.raises(ValueError):
            self.gateway.get_provider("nonexistent")
    
    def test_list_providers(self):
        """测试列出Provider"""
        config1 = LLMConfig(provider=ProviderType.OPENAI)
        config2 = LLMConfig(provider=ProviderType.ANTHROPIC)
        
        self.gateway.register_provider("openai", config1)
        self.gateway.register_provider("anthropic", config2)
        
        providers = self.gateway.list_providers()
        assert len(providers) == 2
        assert "openai" in providers
        assert "anthropic" in providers
    
    def test_usage_stats(self):
        """测试使用统计"""
        config = LLMConfig(provider=ProviderType.OPENAI)
        self.gateway.register_provider("test", config)
        
        stats = self.gateway.get_usage_stats("test")
        assert stats["total_calls"] == 0
        assert stats["total_tokens"] == 0
    
    @patch('httpx.Client')
    def test_chat_call(self, mock_client_class):
        """测试聊天调用"""
        # Mock httpx response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Hello"}, "finish_reason": "stop"}],
            "model": "gpt-4o",
            "usage": {"total_tokens": 100, "prompt_tokens": 50, "completion_tokens": 50}
        }
        mock_response.raise_for_status = Mock()
        
        mock_client = Mock()
        mock_client.post.return_value = mock_response
        mock_client.__enter__ = Mock(return_value=mock_client)
        mock_client.__exit__ = Mock(return_value=False)
        mock_client_class.return_value = mock_client
        
        # 注册Provider
        config = LLMConfig(
            provider=ProviderType.OPENAI,
            api_key="test-key",
            max_retries=1
        )
        self.gateway.register_provider("test", config)
        
        # 调用聊天
        response = self.gateway.chat(
            "test",
            [{"role": "user", "content": "Hello"}],
            system="You are helpful"
        )
        
        assert response.content == "Hello"
        assert response.tokens_used == 100
        assert response.provider == "openai"


# ========== ModelRouter 测试 ==========

class TestModelRouter:
    """模型路由器测试"""
    
    def setup_method(self):
        """测试前准备"""
        self.gateway = ModelGateway()
        self.router = ModelRouter(self.gateway)
    
    def test_configure(self):
        """测试配置"""
        mapping = {
            "planner": "primary",
            "reviewer": "review"
        }
        self.router.configure(mapping)
        assert self.router._role_mapping == mapping
    
    def test_set_role_provider(self):
        """测试设置角色Provider"""
        self.router.set_role_provider(AgentRole.PLANNER, "primary")
        assert self.router.get_provider_name(AgentRole.PLANNER) == "primary"
    
    def test_default_provider(self):
        """测试默认Provider"""
        # 未配置的角色应返回fallback
        provider_name = self.router.get_provider_name(AgentRole.WRITER)
        assert provider_name == "primary"  # fallback


# ========== AgentRouter 测试 ==========

class TestAgentRouter:
    """Agent路由器测试"""
    
    def setup_method(self):
        """测试前准备"""
        self.gateway = ModelGateway()
        router = ModelRouter(self.gateway)
        self.agent_router = AgentRouter(router)
    
    def test_get_planner(self):
        """测试获取规划Agent"""
        planner = self.agent_router.get_planner()
        assert isinstance(planner, AgentClient)
        assert planner.role == AgentRole.PLANNER
    
    def test_get_writer(self):
        """测试获取写作Agent"""
        writer = self.agent_router.get_writer()
        assert isinstance(writer, AgentClient)
        assert writer.role == AgentRole.WRITER
    
    def test_get_reviewer(self):
        """测试获取审查Agent"""
        reviewer = self.agent_router.get_reviewer()
        assert isinstance(reviewer, AgentClient)
        assert reviewer.role == AgentRole.REVIEWER
    
    def test_get_reviser(self):
        """测试获取修订Agent"""
        reviser = self.agent_router.get_reviser()
        assert isinstance(reviser, AgentClient)
        assert reviser.role == AgentRole.REVISER
    
    def test_get_extractor(self):
        """测试获取事实提取Agent"""
        extractor = self.agent_router.get_extractor()
        assert isinstance(extractor, AgentClient)
        assert extractor.role == AgentRole.EXTRACTOR
    
    def test_get_context(self):
        """测试获取上下文Agent"""
        context = self.agent_router.get_context()
        assert isinstance(context, AgentClient)
        assert context.role == AgentRole.CONTEXT


# ========== AgentRole 测试 ==========

class TestAgentRole:
    """Agent角色测试"""
    
    def test_role_values(self):
        """测试角色值"""
        assert AgentRole.PLANNER.value == "planner"
        assert AgentRole.WRITER.value == "writer"
        assert AgentRole.REVIEWER.value == "reviewer"
        assert AgentRole.REVISER.value == "reviser"
        assert AgentRole.EXTRACTOR.value == "extractor"
        assert AgentRole.CONTEXT.value == "context"
        assert AgentRole.FORECAST.value == "forecast"
        assert AgentRole.STYLE.value == "style"


# ========== LLMResponse 测试 ==========

class TestLLMResponse:
    """LLM响应测试"""
    
    def test_response_creation(self):
        """测试创建响应"""
        response = LLMResponse(
            content="Hello",
            model="gpt-4o",
            tokens_used=100,
            latency_ms=500,
            provider="openai"
        )
        assert response.content == "Hello"
        assert response.model == "gpt-4o"
        assert response.tokens_used == 100
        assert response.latency_ms == 500
        assert response.provider == "openai"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
