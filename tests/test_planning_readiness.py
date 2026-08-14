from src.planning.readiness import evaluate_planning_readiness
from src.planning.story_bible import STORY_BIBLE_STEPS


def _steps(*, confirmed=True, volumes=None, arcs=None, chapters=None):
    plans = {
        "volumes": volumes if volumes is not None else [{"title": "第一卷", "goal": "建立核心冲突"}],
        "arcs": arcs if arcs is not None else [{"title": "第一段弧", "goal": "让主角做出第一次不可逆选择"}],
        "chapter_plan": chapters if chapters is not None else [{"chapter": 1, "goal": "主角发现线索"}],
    }
    result = []
    for _, key in STORY_BIBLE_STEPS:
        result.append({
            "step_key": key,
            "status": "confirmed" if confirmed else "draft",
            "draft": plans.get(key, {"content": f"设定：{key}"}),
        })
    return result


def test_managed_creation_requires_all_steps_and_target_coverage():
    readiness = evaluate_planning_readiness(
        _steps(), target_volumes=2, target_chapters=3
    )

    assert readiness["ready"] is False
    assert readiness["storyBibleConfirmed"] == 25
    assert "卷计划不足：需要至少 2 卷，当前 1 卷" in readiness["missingPlan"]
    assert "章节目标不足：需要覆盖 3 章，当前 1 章" in readiness["missingPlan"]


def test_title_only_entries_do_not_satisfy_goal_plan():
    readiness = evaluate_planning_readiness(
        _steps(
            volumes=[{"title": "第一卷"}],
            arcs=[{"title": "第一段弧"}],
            chapters=[{"chapter": 1, "title": "开端"}],
        ),
        target_volumes=1,
        target_chapters=1,
    )

    assert readiness["ready"] is False
    assert readiness["volumeCount"] == 0
    assert readiness["arcCount"] == 0
    assert readiness["chapterPlanCount"] == 0


def test_trusted_import_preserves_explicit_complete_package_compatibility():
    readiness = evaluate_planning_readiness(
        _steps(volumes=[], arcs=[], chapters=[]),
        target_volumes=5,
        target_chapters=100,
        trusted_import=True,
    )

    assert readiness["ready"] is True
    assert readiness["trustedImport"] is True
