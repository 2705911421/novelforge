"""配置管理"""

import os
import yaml
from pathlib import Path
from typing import Optional


class Config:
    """NovelForge 配置管理器"""

    def __init__(self, config_path: Optional[str] = None, project_path: Optional[str] = None):
        self.base_dir = Path(__file__).parent.parent.parent
        self.project_path = Path(project_path) if project_path else Path.cwd()

        # 加载默认配置
        default_config_path = self.base_dir / "config" / "default.yaml"
        self.config = self._load_yaml(default_config_path)

        # 加载自定义配置
        if config_path:
            custom = self._load_yaml(Path(config_path))
            self._deep_merge(self.config, custom)

        # 加载项目配置
        project_config = self.project_path / "novelforge.yaml"
        if project_config.exists():
            project = self._load_yaml(project_config)
            self._deep_merge(self.config, project)

        # 环境变量覆盖
        self._apply_env_overrides()

    def _load_yaml(self, path: Path) -> dict:
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _deep_merge(self, base: dict, override: dict):
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value

    def _apply_env_overrides(self):
        """从环境变量覆盖配置"""
        env_map = {
            "NOVELFORGE_LLM_API_KEY": ("llm", "primary", "api_key"),
            "NOVELFORGE_LLM_BASE_URL": ("llm", "primary", "base_url"),
            "NOVELFORGE_LLM_MODEL": ("llm", "primary", "model"),
            "NOVELFORGE_REVIEW_API_KEY": ("llm", "review", "api_key"),
            "NOVELFORGE_REVIEW_BASE_URL": ("llm", "review", "base_url"),
            "NOVELFORGE_REVIEW_MODEL": ("llm", "review", "model"),
            "OPENAI_API_KEY": ("llm", "primary", "api_key"),
            "OPENAI_BASE_URL": ("llm", "primary", "base_url"),
        }
        for env_key, config_path in env_map.items():
            value = os.environ.get(env_key)
            if value:
                obj = self.config
                for key in config_path[:-1]:
                    obj = obj.setdefault(key, {})
                obj[config_path[-1]] = value

    def get(self, *keys, default=None):
        """获取配置值，支持链式键"""
        obj = self.config
        for key in keys:
            if isinstance(obj, dict):
                obj = obj.get(key)
                if obj is None:
                    return default
            else:
                return default
        return obj

    def set(self, *keys_and_value):
        """设置配置值，最后一个参数是值"""
        if len(keys_and_value) < 2:
            raise ValueError("需要至少一个键和一个值")
        *keys, value = keys_and_value
        obj = self.config
        for key in keys[:-1]:
            obj = obj.setdefault(key, {})
        obj[keys[-1]] = value

    def save(self, path: Optional[str] = None):
        """保存配置到文件"""
        save_path = Path(path) if path else self.project_path / "novelforge.yaml"
        save_path.parent.mkdir(parents=True, exist_ok=True)
        with open(save_path, "w", encoding="utf-8") as f:
            yaml.dump(self.config, f, allow_unicode=True, default_flow_style=False)

    def get_llm_config(self, role: str = "primary") -> dict:
        """获取LLM配置"""
        llm = self.get("llm", role) or self.get("llm", "primary") or {}
        return {
            "provider": llm.get("provider", "openai"),
            "model": llm.get("model", "gpt-4o"),
            "base_url": llm.get("base_url", "https://api.openai.com/v1"),
            "api_key": llm.get("api_key", ""),
            "temperature": llm.get("temperature", 0.8),
            "max_tokens": llm.get("max_tokens", 8000),
        }
