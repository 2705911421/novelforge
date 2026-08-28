from types import SimpleNamespace
from typing import Any, cast

import pytest

from src.core.models import StoryProject
from src.creation.planner import ChapterPlanner
from src.creation.task_handlers import LegacyTaskHandlers
from src.creation.writer import ChapterWriter
from src.llm.dialogue import DialogueWriter, DialogueWriterError
from src.pipeline.observer import Observer


class InvalidJsonModel:
    def get_writer(self):
        return self

    def get_planner(self):
        return self

    def chat_json(self, messages, system=""):
        del messages, system
        return {"raw": "not-json", "error": "JSON parsing failed"}

    def chat(self, messages, system="", **kwargs):
        del messages, system, kwargs
        return SimpleNamespace(content="")


class EmptyMemory:
    def get_chapter_context(self, chapter_number: int, window: int = 3) -> str:
        del chapter_number, window
        return ""


def test_observer_rejects_parser_error_instead_of_returning_empty_facts():
    with pytest.raises(ValueError, match="FACT_EXTRACTION_OUTPUT_INVALID"):
        Observer(cast(Any, InvalidJsonModel())).extract_facts(1, "chapter text")


def test_legacy_chapter_planner_rejects_parser_error():
    with pytest.raises(ValueError, match="CHAPTER_PLAN_OUTPUT_INVALID"):
        ChapterPlanner(cast(Any, InvalidJsonModel())).plan_chapter(
            StoryProject(id="project", name="Test"), 1
        )


def test_legacy_chapter_writer_rejects_empty_content():
    with pytest.raises(ValueError, match="CHAPTER_WRITER_OUTPUT_INVALID"):
        ChapterWriter(cast(Any, InvalidJsonModel()), EmptyMemory()).write_chapter(
            StoryProject(id="project", name="Test"), 1, {}
        )


def test_dialogue_writer_rejects_empty_content():
    with pytest.raises(DialogueWriterError, match="empty content"):
        DialogueWriter(InvalidJsonModel()).generate("林遥", "雨夜的车站")


def test_durable_handler_json_parser_rejects_error_artifact():
    with pytest.raises(ValueError, match="error artifact"):
        LegacyTaskHandlers._parse_json_response('{"error":"provider offline"}')
