"""状态管理 - 管理项目运行时状态"""

import json
from pathlib import Path
from typing import Optional
from datetime import datetime

from .models import StoryProject, ChapterStatus


class StateManager:
    """项目状态管理器"""

    def __init__(self, project_dir: Path):
        self.project_dir = project_dir
        self.state_file = project_dir / "state.json"
        self.state = self._load_state()

    def _load_state(self) -> dict:
        if self.state_file.exists():
            with open(self.state_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {
            "current_phase": "init",  # init/planning/writing/reviewing/exporting
            "current_chapter": 0,
            "continuous_mode": False,
            "continuous_target": 0,
            "continuous_completed": 0,
            "last_joint_review_chapter": 0,
            "total_tokens_used": 0,
            "session_start": datetime.now().isoformat(),
        }

    def save_state(self):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)

    def set_phase(self, phase: str):
        self.state["current_phase"] = phase
        self.save_state()

    def set_current_chapter(self, chapter: int):
        self.state["current_chapter"] = chapter
        self.save_state()

    def start_continuous_mode(self, target: int):
        self.state["continuous_mode"] = True
        self.state["continuous_target"] = target
        self.state["continuous_completed"] = 0
        self.save_state()

    def update_continuous_progress(self, completed: int):
        self.state["continuous_completed"] = completed
        self.save_state()

    def stop_continuous_mode(self):
        self.state["continuous_mode"] = False
        self.save_state()

    def record_joint_review(self, chapter: int):
        self.state["last_joint_review_chapter"] = chapter
        self.save_state()

    def add_tokens(self, count: int):
        self.state["total_tokens_used"] = self.state.get("total_tokens_used", 0) + count
        self.save_state()

    def get_status(self) -> dict:
        return self.state.copy()
