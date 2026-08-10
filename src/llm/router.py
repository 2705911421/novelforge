"""
NovelForge Model Router
按Agent角色智能路由到不同Provider
"""

import logging
from typing import Optional, Dict, List
from enum import Enum

from .gateway import ModelGateway, LLMResponse, get_gateway
from .agent_prompts import compose_agent_prompt

logger = logging.getLogger(__name__)


class AgentRole(str, Enum):
    """Agent角色"""
    PLANNER = "planner"          # 规划Agent
    WRITER = "writer"            # 写作Agent
    REVIEWER = "reviewer"        # 审查Agent
    REVISER = "reviser"          # 修订Agent
    EXTRACTOR = "extractor"      # 事实提取Agent
    CONTEXT = "context"          # 上下文Agent
    FORECAST = "forecast"        # 剧情预测Agent
    STYLE = "style"              # 风格分析Agent


class ModelRouter:
    """模型路由器 - 按Agent角色路由到最优Provider"""
    
    def __init__(self, gateway: Optional[ModelGateway] = None):
        self.gateway = gateway or get_gateway()
        self._role_mapping: Dict[str, str] = {}
        self._fallback_provider = "primary"
    
    def configure(self, role_mapping: Dict[str, str]):
        """
        配置角色映射
        
        Args:
            role_mapping: 角色到Provider的映射，例如 {"planner": "primary", "reviewer": "review"}
        """
        self._role_mapping = role_mapping
        logger.info(f"配置角色映射: {role_mapping}")
    
    def set_role_provider(self, role: AgentRole, provider_name: str):
        """设置单个角色的Provider"""
        self._role_mapping[role.value] = provider_name
    
    def get_provider_name(self, role: AgentRole) -> str:
        """获取角色对应的Provider名称"""
        return self._role_mapping.get(role.value, self._fallback_provider)

    @staticmethod
    def _contract_role(role: AgentRole) -> str:
        return {
            AgentRole.EXTRACTOR: "fact_extraction",
            AgentRole.FORECAST: "planner",
            AgentRole.STYLE: "context",
        }.get(role, role.value)

    def _effective_system(self, role: AgentRole, system: str) -> str:
        """Keep the legacy in-memory gateway on the same contract boundary."""
        return compose_agent_prompt(self._contract_role(role), system)
    
    def chat(self, role: AgentRole, messages: List[Dict], 
             system: str = "", **kwargs) -> LLMResponse:
        """按角色调用聊天"""
        provider_name = self.get_provider_name(role)
        logger.debug(f"路由 {role.value} -> {provider_name}")
        return self.gateway.chat(provider_name, messages, self._effective_system(role, system), **kwargs)
    
    def chat_json(self, role: AgentRole, messages: List[Dict],
                  system: str = "", **kwargs) -> Dict:
        """按角色调用聊天，返回JSON"""
        provider_name = self.get_provider_name(role)
        logger.debug(f"路由 {role.value} -> {provider_name} (JSON)")
        return self.gateway.chat_json(provider_name, messages, self._effective_system(role, system), **kwargs)
    
    def chat_stream(self, role: AgentRole, messages: List[Dict],
                    system: str = "", **kwargs):
        """按角色流式聊天"""
        provider_name = self.get_provider_name(role)
        logger.debug(f"路由 {role.value} -> {provider_name} (stream)")
        return self.gateway.chat_stream(provider_name, messages, self._effective_system(role, system), **kwargs)
    
    def get_usage_by_role(self) -> Dict[str, Dict]:
        """获取按角色分组的使用统计"""
        stats = {}
        for role, provider_name in self._role_mapping.items():
            provider_stats = self.gateway.get_usage_stats(provider_name)
            if provider_stats:
                stats[role] = provider_stats
        return stats


class AgentRouter:
    """Agent路由器 - 高层封装，为每个Agent提供专用接口"""
    
    def __init__(self, router: Optional[ModelRouter] = None):
        self.router = router or ModelRouter()
    
    def get_planner(self) -> 'AgentClient':
        """获取规划Agent客户端"""
        return AgentClient(self.router, AgentRole.PLANNER)
    
    def get_writer(self) -> 'AgentClient':
        """获取写作Agent客户端"""
        return AgentClient(self.router, AgentRole.WRITER)
    
    def get_reviewer(self) -> 'AgentClient':
        """获取审查Agent客户端"""
        return AgentClient(self.router, AgentRole.REVIEWER)
    
    def get_reviser(self) -> 'AgentClient':
        """获取修订Agent客户端"""
        return AgentClient(self.router, AgentRole.REVISER)
    
    def get_extractor(self) -> 'AgentClient':
        """获取事实提取Agent客户端"""
        return AgentClient(self.router, AgentRole.EXTRACTOR)
    
    def get_context(self) -> 'AgentClient':
        """获取上下文Agent客户端"""
        return AgentClient(self.router, AgentRole.CONTEXT)
    
    def get_forecast(self) -> 'AgentClient':
        """获取剧情预测Agent客户端"""
        return AgentClient(self.router, AgentRole.FORECAST)
    
    def get_style(self) -> 'AgentClient':
        """获取风格分析Agent客户端"""
        return AgentClient(self.router, AgentRole.STYLE)


class AgentClient:
    """Agent客户端 - 为特定角色封装的LLM客户端"""
    
    def __init__(self, router: ModelRouter, role: AgentRole):
        self.router = router
        self.role = role
    
    def chat(self, messages: List[Dict], system: str = "", **kwargs) -> LLMResponse:
        """聊天"""
        return self.router.chat(self.role, messages, system, **kwargs)
    
    def chat_json(self, messages: List[Dict], system: str = "", **kwargs) -> Dict:
        """聊天返回JSON"""
        return self.router.chat_json(self.role, messages, system, **kwargs)
    
    def chat_stream(self, messages: List[Dict], system: str = "", **kwargs):
        """流式聊天"""
        return self.router.chat_stream(self.role, messages, system, **kwargs)


# 全局Agent路由器实例
_agent_router: Optional[AgentRouter] = None


def get_agent_router() -> AgentRouter:
    """获取全局Agent路由器"""
    global _agent_router
    if _agent_router is None:
        _agent_router = AgentRouter()
    return _agent_router


def init_agent_router_from_config(config) -> AgentRouter:
    """从配置初始化Agent路由器"""
    from .gateway import init_gateway_from_config
    
    # 初始化网关
    gateway = init_gateway_from_config(config)
    
    # 创建路由器
    router = ModelRouter(gateway)
    
    # 配置角色映射
    role_mapping = {
        "planner": "primary",
        "writer": "primary",
        "reviewer": "review",
        "reviser": "primary",
        "extractor": "primary",
        "context": "primary",
        "forecast": "primary",
        "style": "primary",
    }
    
    # 从配置覆盖
    custom_mapping = config.get("llm", "routing", default={})
    if custom_mapping:
        role_mapping.update(custom_mapping)
    
    router.configure(role_mapping)
    
    global _agent_router
    _agent_router = AgentRouter(router)
    
    return _agent_router
