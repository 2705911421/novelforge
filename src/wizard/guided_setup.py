"""世界观向导 - 引导用户与AI协同构建完整世界观"""

import json
from typing import Optional
from pathlib import Path

from ..core.models import (
    StoryProject, WorldSetting, Character, Faction,
    Location, Foreshadowing, Volume, Arc
)
from ..core.project import ProjectManager
from ..llm.client import MultiModelManager
from ..llm.prompts import PromptManager


class WorldWizard:
    """世界观构建向导"""

    def __init__(self, model_manager: MultiModelManager, project_manager: ProjectManager):
        self.models = model_manager
        self.projects = project_manager
        self.prompts = PromptManager()

    def build_world(self, user_input: str, project: StoryProject,
                    existing_data: dict = None) -> dict:
        """构建完整世界观设定

        Args:
            user_input: 用户提供的初始设定描述（可以是txt/md/docx导入的内容）
            project: 项目对象
            existing_data: 已有的设定数据（用于增量构建）

        Returns:
            完整的世界观设定字典
        """
        client = self.models.get_writer()

        system_prompt = self.prompts.load("world_wizard")

        messages = [
            {"role": "user", "content": f"""
请根据以下描述，构建完整的小说世界观设定：

## 用户描述
{user_input}

## 已有设定（如有）
{json.dumps(existing_data, ensure_ascii=False, indent=2) if existing_data else "无"}

请输出完整的JSON格式世界观设定，包含以下模块：
1. world: 世界基础设定（名称、类型、背景、核心矛盾、力量体系、世界规则）
2. characters: 角色设定列表（每个角色包含name/role/description/personality/background/abilities/relationships）
3. factions: 势力设定列表（每个势力包含name/description/leader/members/allies/enemies/territory/goals）
4. locations: 地点设定列表（每个地点包含name/description/connected_to/faction/significance）
5. volumes: 卷规划列表（每卷包含title/description/arcs/themes/target_chapters）
6. timeline: 初始时间线事件
7. foreshadowing: 初始伏笔设计列表
8. writing_style: 写作风格要求
9. core_conflict: 核心矛盾描述
10. author_intent: 作者意图

请确保所有内容都以JSON格式输出。
"""}
        ]

        response = client.chat_json(messages, system_prompt)

        # 解析并应用到项目
        if "error" not in response:
            self._apply_world_data(project, response)

        return response

    def import_world_file(self, file_path: str) -> str:
        """导入世界设定文件"""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        content = ""
        suffix = path.suffix.lower()

        if suffix in (".md", ".txt"):
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        elif suffix == ".docx":
            content = self._read_docx(path)
        else:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

        return content

    def _read_docx(self, path: Path) -> str:
        """读取docx文件"""
        try:
            import docx
            doc = docx.Document(str(path))
            return "\n".join([para.text for para in doc.paragraphs])
        except ImportError:
            raise ImportError("需要安装 python-docx: pip install python-docx")

    def refine_world(self, project: StoryProject, feedback: str) -> dict:
        """根据用户反馈精炼世界观"""
        client = self.models.get_writer()
        system_prompt = self.prompts.load("world_wizard")

        current_world = project.to_dict()

        messages = [
            {"role": "user", "content": f"""
当前世界观设定：
{json.dumps(current_world, ensure_ascii=False, indent=2)}

用户反馈/修改意见：
{feedback}

请根据反馈修改世界观设定，输出完整的更新后JSON。
"""}
        ]

        response = client.chat_json(messages, system_prompt)
        if "error" not in response:
            self._apply_world_data(project, response)

        return response

    def _apply_world_data(self, project: StoryProject, data: dict):
        """将世界观数据应用到项目"""
        # 世界设定
        if "world" in data:
            w = data["world"]
            project.world = WorldSetting(
                name=w.get("name", project.name),
                genre=w.get("genre", project.genre),
                setting_description=w.get("setting_description", ""),
                time_period=w.get("time_period", ""),
                core_conflict=w.get("core_conflict", ""),
                power_system=w.get("power_system", ""),
                world_rules=w.get("world_rules", []),
                cultures=w.get("cultures", []),
                history=w.get("history", ""),
                themes=w.get("themes", []),
            )

        # 核心矛盾
        if "core_conflict" in data:
            project.world.core_conflict = data["core_conflict"]

        # 写作风格
        if "writing_style" in data:
            project.writing_style = data["writing_style"]

        # 作者意图
        if "author_intent" in data:
            project.author_intent = data["author_intent"]

        # 角色
        if "characters" in data:
            chars = data["characters"]
            if isinstance(chars, list):
                for c in chars:
                    char = Character(
                        name=c.get("name", ""),
                        role=c.get("role", ""),
                        description=c.get("description", ""),
                        personality=c.get("personality", ""),
                        background=c.get("background", ""),
                        abilities=c.get("abilities", []),
                        relationships=c.get("relationships", {}),
                        faction=c.get("faction", ""),
                    )
                    project.characters[char.name] = char
            elif isinstance(chars, dict):
                for name, c in chars.items():
                    char = Character(
                        name=name,
                        role=c.get("role", ""),
                        description=c.get("description", ""),
                        personality=c.get("personality", ""),
                        background=c.get("background", ""),
                        abilities=c.get("abilities", []),
                        relationships=c.get("relationships", {}),
                        faction=c.get("faction", ""),
                    )
                    project.characters[name] = char

        # 势力
        if "factions" in data:
            factions = data["factions"]
            if isinstance(factions, list):
                for f in factions:
                    faction = Faction(
                        name=f.get("name", ""),
                        description=f.get("description", ""),
                        leader=f.get("leader", ""),
                        members=f.get("members", []),
                        allies=f.get("allies", []),
                        enemies=f.get("enemies", []),
                        territory=f.get("territory", ""),
                        goals=f.get("goals", []),
                    )
                    project.factions[faction.name] = faction

        # 地点
        if "locations" in data:
            locs = data["locations"]
            if isinstance(locs, list):
                for l in locs:
                    loc = Location(
                        name=l.get("name", ""),
                        description=l.get("description", ""),
                        connected_to=l.get("connected_to", []),
                        faction=l.get("faction", ""),
                        significance=l.get("significance", ""),
                    )
                    project.locations[loc.name] = loc

        # 卷规划
        if "volumes" in data:
            vols = data["volumes"]
            if isinstance(vols, list):
                for i, v in enumerate(vols, 1):
                    vol = Volume(
                        number=i,
                        title=v.get("title", f"第{i}卷"),
                        description=v.get("description", ""),
                        themes=v.get("themes", []),
                        target_chapters=v.get("target_chapters", 10),
                    )
                    if "arcs" in v:
                        for a in v["arcs"]:
                            arc = Arc(
                                name=a.get("name", ""),
                                volume=i,
                                description=a.get("description", ""),
                                key_events=a.get("key_events", []),
                                themes=a.get("themes", []),
                            )
                            vol.arcs.append(arc)
                    project.volumes.append(vol)

        # 伏笔
        if "foreshadowing" in data:
            fs_list = data["foreshadowing"]
            if isinstance(fs_list, list):
                for i, fs in enumerate(fs_list):
                    foreshadowing = Foreshadowing(
                        id=f"fs_{i+1:03d}",
                        description=fs.get("description", ""),
                        status=fs.get("status", "open"),
                        related_characters=fs.get("related_characters", []),
                    )
                    project.foreshadowing[foreshadowing.id] = foreshadowing

        # 时间线
        if "timeline" in data:
            project.timeline = data["timeline"]

    def generate_mindmap_data(self, project: StoryProject) -> dict:
        """生成思维导图数据"""
        mindmap = {
            "root": {
                "text": project.name,
                "children": []
            }
        }

        # 世界观分支
        world_branch = {"text": "世界观设定", "children": []}
        if project.world.core_conflict:
            world_branch["children"].append({"text": f"核心矛盾: {project.world.core_conflict}"})
        if project.world.power_system:
            world_branch["children"].append({"text": f"力量体系: {project.world.power_system}"})
        if project.world.world_rules:
            rules_branch = {"text": "世界规则", "children": [{"text": r} for r in project.world.world_rules]}
            world_branch["children"].append(rules_branch)
        mindmap["root"]["children"].append(world_branch)

        # 角色分支
        if project.characters:
            char_branch = {"text": "人物关系", "children": []}
            for name, char in project.characters.items():
                char_node = {
                    "text": f"{name} ({char.role})",
                    "children": []
                }
                if char.relationships:
                    for rel_name, rel_type in char.relationships.items():
                        char_node["children"].append({"text": f"→ {rel_name}: {rel_type}"})
                char_branch["children"].append(char_node)
            mindmap["root"]["children"].append(char_branch)

        # 势力分支
        if project.factions:
            faction_branch = {"text": "势力分布", "children": []}
            for name, faction in project.factions.items():
                faction_node = {"text": name, "children": []}
                if faction.leader:
                    faction_node["children"].append({"text": f"领袖: {faction.leader}"})
                if faction.allies:
                    faction_node["children"].append({"text": f"盟友: {', '.join(faction.allies)}"})
                if faction.enemies:
                    faction_node["children"].append({"text": f"敌对: {', '.join(faction.enemies)}"})
                faction_branch["children"].append(faction_node)
            mindmap["root"]["children"].append(faction_branch)

        # 地图分支
        if project.locations:
            map_branch = {"text": "地图设定", "children": []}
            for name, loc in project.locations.items():
                loc_node = {"text": name, "children": []}
                if loc.faction:
                    loc_node["children"].append({"text": f"所属: {loc.faction}"})
                if loc.connected_to:
                    loc_node["children"].append({"text": f"连接: {', '.join(loc.connected_to)}"})
                map_branch["children"].append(loc_node)
            mindmap["root"]["children"].append(map_branch)

        # 故事结构分支
        if project.volumes:
            story_branch = {"text": "故事结构", "children": []}
            for vol in project.volumes:
                vol_node = {"text": vol.title, "children": []}
                for arc in vol.arcs:
                    arc_node = {"text": arc.name, "children": []}
                    if arc.key_events:
                        for event in arc.key_events:
                            arc_node["children"].append({"text": event})
                    vol_node["children"].append(arc_node)
                story_branch["children"].append(vol_node)
            mindmap["root"]["children"].append(story_branch)

        # 伏笔分支
        open_hooks = project.get_open_foreshadowing()
        if open_hooks:
            hook_branch = {"text": "伏笔与钩子", "children": []}
            for hook in open_hooks:
                hook_branch["children"].append({
                    "text": f"[{hook.id}] {hook.description}",
                })
            mindmap["root"]["children"].append(hook_branch)

        return mindmap
