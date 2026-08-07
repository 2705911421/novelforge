"""验证所有模块可正常导入"""
import os
os.environ["PYTHONIOENCODING"] = "utf-8"
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

OK = "[OK]"
FAIL = "[FAIL]"

def test_imports():
    from src.core.models import StoryProject, Chapter, Character, Faction, Location, Foreshadowing, Volume, Arc, WorldSetting
    from src.core.config import Config
    from src.core.project import ProjectManager
    from src.core.memory import MemorySystem
    from src.core.state import StateManager
    print(f'{OK} Core modules imported')

    from src.llm.client import LLMClient, MultiModelManager
    from src.llm.prompts import PromptManager
    print(f'{OK} LLM modules imported')

    from src.wizard.guided_setup import WorldWizard
    print(f'{OK} Wizard module imported')

    from src.review.reviewer import ChapterReviewer
    from src.review.joint_reviewer import JointReviewer
    print(f'{OK} Review modules imported')

    from src.creation.planner import ChapterPlanner
    from src.creation.writer import ChapterWriter
    from src.creation.continuous import ContinuousCreationMode
    print(f'{OK} Creation engine imported')

    from src.export.exporter import Exporter
    print(f'{OK} Export module imported')

    from src.visualization.mindmap import MindMapGenerator, TimelineGenerator
    print(f'{OK} Visualization module imported')

    # 测试数据模型
    project = StoryProject(id="test", name="Test Novel", genre="Fantasy")
    char = Character(name="Hero", role="protagonist", personality="brave")
    project.characters["Hero"] = char
    assert project.get_chapter_count() == 0
    assert project.get_latest_chapter_number() == 0
    print(f'{OK} Data models test passed')

    # 测试记忆系统
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        memory = MemorySystem(Path(tmpdir))
        memory.store_chapter_summary(1, "Chapter 1 summary", ["Event1"], ["Char1"], ["Loc1"])
        summaries = memory.get_recent_summaries(1)
        assert len(summaries) == 1
        assert summaries[0]["summary"] == "Chapter 1 summary"
    print(f'{OK} Memory system test passed')

    # 测试配置
    config = Config()
    assert config.get("project", "chapter_words_min") == 2000
    assert config.get("review", "pass_score") == 93
    print(f'{OK} Config system test passed')

    # 测试状态管理
    with tempfile.TemporaryDirectory() as tmpdir:
        state = StateManager(Path(tmpdir))
        state.set_phase("writing")
        state.set_current_chapter(5)
        assert state.get_status()["current_chapter"] == 5
    print(f'{OK} State manager test passed')

    # 测试导出器
    exporter = Exporter()
    print(f'{OK} Exporter initialized')

    # 测试可视化
    mm = MindMapGenerator()
    tl = TimelineGenerator()
    print(f'{OK} Visualization initialized')

    print(f'\n=== ALL TESTS PASSED ===')

if __name__ == "__main__":
    test_imports()
