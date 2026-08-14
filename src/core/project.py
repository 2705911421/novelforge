"""项目管理 - 负责项目的创建、加载、保存"""

import json
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional

from .models import (
    Chapter,
    ChapterReview,
    ChapterStatus,
    Character,
    Faction,
    Foreshadowing,
    Location,
    ReviewDimension,
    ReviewVerdict,
    StoryProject,
    WorldSetting,
)
from .config import Config
from .story_repository import StoryRepository


class ProjectManager:
    """项目管理器"""

    def __init__(self, base_dir: str = ".", repository: Optional[StoryRepository] = None):
        self.base_dir = Path(base_dir)
        self.projects_dir = self.base_dir / "projects"
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        # Native projects and explicitly migrated projects use SQLite as their
        # structured truth source. Unmigrated files remain legacy inputs until
        # a user explicitly confirms a migration.
        self.story_repository = repository or StoryRepository()

    def create_project(
        self,
        name: str,
        genre: str = "",
        config: Optional[Config] = None,
        *,
        target_chapters: Optional[int] = None,
        target_volumes: Optional[int] = None,
        chapter_word_target: Optional[int] = None,
        language: Optional[str] = None,
        style_profile: Optional[dict] = None,
    ) -> StoryProject:
        """创建新项目"""
        chapter_words_min = self._config_int(config, "chapter_words_min", 2000)
        chapter_words_max = self._config_int(config, "chapter_words_max", 4000)
        configured_target_chapters = self._config_int(config, "target_chapters", 100)
        resolved_target_chapters = self._positive_int(target_chapters, configured_target_chapters)
        configured_target_volumes = self._config_int(config, "target_volumes", 5)
        resolved_target_volumes = self._positive_int(target_volumes, configured_target_volumes)
        resolved_chapter_word_target = self._positive_int(chapter_word_target, 0)
        configured_language = config.get("project", "language", default="zh-CN") if config else "zh-CN"
        resolved_language = (
            language.strip() if isinstance(language, str) and language.strip()
            else configured_language if isinstance(configured_language, str) and configured_language.strip()
            else "zh-CN"
        )
        project_id = self.story_repository.create_native_project(
            name, genre, target_chapters=resolved_target_chapters, chapter_words_min=chapter_words_min,
            chapter_words_max=chapter_words_max,
            target_word_count=resolved_target_chapters * resolved_chapter_word_target,
            target_volumes=resolved_target_volumes,
            language=resolved_language,
            style_profile=style_profile if isinstance(style_profile, dict) else {},
        )
        project_dir = self.projects_dir / project_id
        project_dir.mkdir(parents=True, exist_ok=True)

        # Files hold attachments/exports/backups, never structured story truth.
        (project_dir / "attachments").mkdir(exist_ok=True)
        (project_dir / "exports").mkdir(exist_ok=True)
        (project_dir / "backups").mkdir(exist_ok=True)

        project = self.story_repository.load_authoritative_project(project_id)
        if project is None:
            raise RuntimeError("native project creation was not persisted")

        # 保存项目配置
        if config:
            config.save(str(project_dir / "novelforge.yaml"))

        return project

    @staticmethod
    def _config_int(config: Optional[Config], key: str, default: int) -> int:
        if config is None:
            return default
        value = config.get("project", key, default=default)
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else default

    @staticmethod
    def _positive_int(value: Optional[int], default: int) -> int:
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else default

    def _validate_project_id(self, project_id: str) -> bool:
        """验证project_id安全性，防止路径穿越"""
        import re
        # 只允许字母数字和连字符
        return bool(re.match(r'^[a-zA-Z0-9\-]+$', project_id))

    def load_project(self, project_id: str) -> Optional[StoryProject]:
        """加载项目"""
        if not self._validate_project_id(project_id):
            return None
        if self.story_repository.is_authoritative_project(project_id):
            return self.story_repository.load_authoritative_project(project_id)
        project_file = self.projects_dir / project_id / "project.json"
        if not project_file.exists():
            return None

        with open(project_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 重建项目对象
        project = self._dict_to_project(data)
        return project

    def save_project(self, project: StoryProject):
        """保存项目"""
        if self.story_repository.is_authoritative_project(project.id):
            self.story_repository.save_authoritative_project(project)
            return
        project_dir = self.projects_dir / project.id
        project_dir.mkdir(parents=True, exist_ok=True)

        project.updated_at = datetime.now().isoformat()

        project_file = project_dir / "project.json"
        with open(project_file, "w", encoding="utf-8") as f:
            json.dump(project.to_dict(), f, ensure_ascii=False, indent=2)

    def list_projects(self) -> list:
        """列出所有项目"""
        projects = self.story_repository.list_authoritative_projects()
        known_ids = {project["id"] for project in projects}
        if not self.projects_dir.exists():
            return projects
        for project_dir in self.projects_dir.iterdir():
            if project_dir.is_dir() and project_dir.name not in known_ids:
                project_file = project_dir / "project.json"
                if project_file.exists():
                    with open(project_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    projects.append({
                        "id": data.get("id"),
                        "name": data.get("name"),
                        "genre": data.get("genre"),
                        "chapters": len(data.get("chapters", {})),
                        "target_chapters": data.get("target_chapters", 100),
                        "target_volumes": data.get("target_volumes", 5),
                        "target_word_count": data.get("target_word_count", 0),
                        "language": data.get("language", "zh-CN"),
                        "created_at": data.get("created_at"),
                        "updated_at": data.get("updated_at"),
                    })
        return sorted(projects, key=lambda x: x.get("updated_at", ""), reverse=True)

    def delete_project(self, project_id: str) -> bool:
        """删除项目"""
        if not self._validate_project_id(project_id):
            return False
        project_dir = self.projects_dir / project_id
        if self.story_repository.is_authoritative_project(project_id):
            deleted = self.story_repository.delete_authoritative_project(project_id)
            if project_dir.exists():
                shutil.rmtree(project_dir)
            return deleted
        if not project_dir.exists():
            return False
        try:
            shutil.rmtree(project_dir)
            return True
        except (PermissionError, OSError):
            # 某些挂载文件系统不支持基于文件描述符的目录删除，
            # 退化为自底向上的手动删除（先删文件再删目录）。
            import os
            for root, dirs, files in os.walk(project_dir, topdown=False):
                for name in files:
                    try:
                        os.remove(os.path.join(root, name))
                    except OSError:
                        pass
                for name in dirs:
                    try:
                        os.rmdir(os.path.join(root, name))
                    except OSError:
                        pass
            try:
                os.rmdir(project_dir)
            except OSError:
                pass
            return not project_dir.exists()

    def get_project_dir(self, project_id: str) -> Path:
        """获取项目目录"""
        if not self._validate_project_id(project_id):
            raise ValueError(f"Invalid project_id: {project_id}")
        return self.projects_dir / project_id

    def save_chapter_content(
        self,
        project_id: str,
        chapter_number: int,
        content: str,
        *,
        title: str = "",
        expected_version: Optional[int] = None,
        status: Optional[str] = None,
    ):
        """保存章节正文到独立文件"""
        if not self._validate_project_id(project_id):
            return
        if self.story_repository.is_authoritative_project(project_id):
            self.story_repository.save_chapter_content(
                project_id, chapter_number, content, title=title,
                expected_version=expected_version, status=status,
            )
            return
        chapters_dir = self.projects_dir / project_id / "chapters"
        chapters_dir.mkdir(parents=True, exist_ok=True)
        chapter_file = chapters_dir / f"chapter_{chapter_number:04d}.md"
        with open(chapter_file, "w", encoding="utf-8") as f:
            f.write(content)

    def load_chapter_content(self, project_id: str, chapter_number: int) -> str:
        """加载章节正文"""
        if not self._validate_project_id(project_id):
            return ""
        if self.story_repository.is_authoritative_project(project_id):
            book = self.story_repository.book_for_project(project_id)
            if not book:
                return ""
            chapter = self.story_repository.db.fetchone(
                "SELECT content FROM chapters WHERE book_id=? AND number=?", (book["id"], chapter_number)
            )
            return chapter["content"] if chapter else ""
        chapter_file = self.projects_dir / project_id / "chapters" / f"chapter_{chapter_number:04d}.md"
        if chapter_file.exists():
            with open(chapter_file, "r", encoding="utf-8") as f:
                return f.read()
        return ""

    def save_review(self, project_id: str, review_data: dict):
        """保存审查报告"""
        if not self._validate_project_id(project_id):
            return
        if self.story_repository.is_authoritative_project(project_id):
            self.story_repository.save_review(project_id, review_data)
            return
        reviews_dir = self.projects_dir / project_id / "reviews"
        reviews_dir.mkdir(parents=True, exist_ok=True)
        chapter_num = review_data.get("chapter_number", 0)
        review_file = reviews_dir / f"review_chapter_{chapter_num:04d}.json"
        with open(review_file, "w", encoding="utf-8") as f:
            json.dump(review_data, f, ensure_ascii=False, indent=2)

    def delete_chapter(self, project_id: str, chapter_number: int) -> bool:
        """Delete a chapter through its owning truth source."""
        if not self._validate_project_id(project_id):
            return False
        if self.story_repository.is_authoritative_project(project_id):
            return self.story_repository.delete_chapter(project_id, chapter_number)
        project = self.load_project(project_id)
        if project is None or chapter_number not in project.chapters:
            return False
        del project.chapters[chapter_number]
        self.save_project(project)
        chapter_file = self.projects_dir / project_id / "chapters" / f"chapter_{chapter_number:04d}.md"
        if chapter_file.exists():
            chapter_file.unlink()
        return True

    def save_joint_review(self, project_id: str, chapter_range: str, review_data: dict):
        """保存联合审查报告"""
        if not self._validate_project_id(project_id):
            return
        import re
        # 校验chapter_range只允许数字和连字符
        if not re.match(r'^[0-9\-]+$', chapter_range):
            return
        reviews_dir = self.projects_dir / project_id / "reviews"
        reviews_dir.mkdir(parents=True, exist_ok=True)
        review_file = reviews_dir / f"joint_review_{chapter_range}.json"
        with open(review_file, "w", encoding="utf-8") as f:
            json.dump(review_data, f, ensure_ascii=False, indent=2)

    def _dict_to_project(self, data: dict) -> StoryProject:
        """将字典转换为StoryProject对象"""
        project = StoryProject(
            id=data.get("id", ""),
            name=data.get("name", ""),
            genre=data.get("genre", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            writing_style=data.get("writing_style", ""),
            style_profile=data.get("style_profile", {}) if isinstance(data.get("style_profile", {}), dict) else {},
            target_word_count=data.get("target_word_count", 0),
            target_chapters=data.get("target_chapters", 100),
            target_volumes=data.get("target_volumes", 5),
            language=data.get("language", "zh-CN"),
            author_intent=data.get("author_intent", ""),
            timeline=data.get("timeline", []),
        )

        # 恢复世界设定
        world_data = data.get("world", {})
        project.world = WorldSetting(**{k: v for k, v in world_data.items() if k in WorldSetting.__dataclass_fields__})

        # 恢复角色
        for name, char_data in data.get("characters", {}).items():
            project.characters[name] = Character(**{k: v for k, v in char_data.items() if k in Character.__dataclass_fields__})

        # 恢复势力
        for name, faction_data in data.get("factions", {}).items():
            project.factions[name] = Faction(**{k: v for k, v in faction_data.items() if k in Faction.__dataclass_fields__})

        # 恢复地点
        for name, loc_data in data.get("locations", {}).items():
            project.locations[name] = Location(**{k: v for k, v in loc_data.items() if k in Location.__dataclass_fields__})

        # 恢复伏笔
        for fid, fs_data in data.get("foreshadowing", {}).items():
            project.foreshadowing[fid] = Foreshadowing(**{k: v for k, v in fs_data.items() if k in Foreshadowing.__dataclass_fields__})

        # 恢复章节
        for num_str, ch_data in data.get("chapters", {}).items():
            ch = Chapter(number=int(num_str))
            for k, v in ch_data.items():
                if k == "status":
                    ch.status = ChapterStatus(v)
                elif k == "review" and v:
                    # 恢复ChapterReview
                    review = ChapterReview(chapter_number=v.get("chapter_number", int(num_str)))
                    review.overall_score = v.get("overall_score", 0)
                    review.specific_issues = v.get("specific_issues", [])
                    review.revision_suggestions = v.get("revision_suggestions", [])
                    review.timestamp = v.get("timestamp", "")
                    verdict_str = v.get("verdict", "needs_revision")
                    try:
                        review.verdict = ReviewVerdict(verdict_str)
                    except ValueError:
                        review.verdict = ReviewVerdict.NEEDS_REVISION
                    # 恢复维度评分
                    for dim_data in v.get("dimensions", []):
                        dim = ReviewDimension(
                            name=dim_data.get("name", ""),
                            score=dim_data.get("score", 0),
                            issues=dim_data.get("issues", []),
                            suggestions=dim_data.get("suggestions", []),
                        )
                        review.dimensions.append(dim)
                    ch.review = review
                elif hasattr(ch, k):
                    setattr(ch, k, v)
            project.chapters[int(num_str)] = ch

        return project
