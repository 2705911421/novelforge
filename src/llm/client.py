"""LLM统一客户端 - 支持OpenAI兼容接口"""

import json
import httpx
from dataclasses import dataclass
from typing import Optional


@dataclass
class LLMResponse:
    """LLM响应"""
    content: str
    model: str = ""
    tokens_used: int = 0
    finish_reason: str = ""


class LLMClient:
    """统一LLM客户端，支持任何OpenAI兼容接口"""

    def __init__(self, config: dict):
        self.provider = config.get("provider", "openai")
        self.model = config.get("model", "gpt-4o")
        self.base_url = config.get("base_url", "https://api.openai.com/v1").rstrip("/")
        self.api_key = config.get("api_key", "")
        self.temperature = config.get("temperature", 0.8)
        self.max_tokens = config.get("max_tokens", 8000)

    def chat(self, messages: list, system: str = "", temperature: Optional[float] = None,
             max_tokens: Optional[int] = None, json_mode: bool = False) -> LLMResponse:
        """同步聊天接口"""
        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        formatted_messages = []
        if system:
            formatted_messages.append({"role": "system", "content": system})
        formatted_messages.extend(messages)

        body = {
            "model": self.model,
            "messages": formatted_messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
        }

        if json_mode:
            body["response_format"] = {"type": "json_object"}

        try:
            with httpx.Client(timeout=300) as client:
                response = client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=body
                )
                response.raise_for_status()
                data = response.json()

            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})

            return LLMResponse(
                content=content,
                model=data.get("model", self.model),
                tokens_used=usage.get("total_tokens", 0),
                finish_reason=data["choices"][0].get("finish_reason", ""),
            )
        except Exception as e:
            raise RuntimeError(f"LLM调用失败: {e}")

    def chat_json(self, messages: list, system: str = "", temperature: Optional[float] = None) -> dict:
        """聊天并返回JSON"""
        response = self.chat(messages, system, temperature, json_mode=True)
        try:
            # 尝试从响应中提取JSON
            content = response.content.strip()
            if content.startswith("```"):
                # 移除代码块标记
                lines = content.split("\n")
                content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            return json.loads(content)
        except json.JSONDecodeError:
            # 尝试从文本中提取JSON
            try:
                start = content.index("{")
                end = content.rindex("}") + 1
                return json.loads(content[start:end])
            except (ValueError, json.JSONDecodeError):
                return {"raw": content, "error": "JSON解析失败"}

    async def chat_stream(self, messages: list, system: str = "",
                    temperature: Optional[float] = None):
        """流式聊天接口（同步包装）"""
        # 简化实现：使用非流式调用，分段返回
        response = self.chat(messages, system, temperature)
        # 模拟流式输出
        content = response.content
        chunk_size = 50
        for i in range(0, len(content), chunk_size):
            yield content[i:i + chunk_size]


class MultiModelManager:
    """多模型管理器 - 为不同角色分配不同模型"""

    def __init__(self, config):
        self.config = config
        self._clients = {}

    def get_client(self, role: str = "primary") -> LLMClient:
        """获取指定角色的LLM客户端"""
        if role not in self._clients:
            llm_config = self.config.get_llm_config(role)
            self._clients[role] = LLMClient(llm_config)
        return self._clients[role]

    def get_writer(self) -> LLMClient:
        """获取写手模型"""
        return self.get_client("primary")

    def get_reviewer(self) -> LLMClient:
        """获取审查模型"""
        return self.get_client("review")

    def get_planner(self) -> LLMClient:
        """获取规划模型"""
        return self.get_client("primary")
