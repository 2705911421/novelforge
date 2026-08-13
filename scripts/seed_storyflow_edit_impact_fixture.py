"""Create a disposable StoryFlow fixture with a real chapter edit boundary.

This extends the regular 120-chapter browser fixture with two immutable
ChapterVersions, a superseded baseline StoryCommit, an accepted revised
StoryCommit, and the resulting durable state boundaries.  It is only for
browser acceptance evidence; it never changes product runtime data.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.seed_storyflow_browser_fixture import seed  # noqa: E402
from src.core.database import Database  # noqa: E402
from src.core.story_repository import StoryRepository  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--chapters", type=int, default=120)
    parser.add_argument("--chapter", type=int, default=87)
    args = parser.parse_args()
    if args.chapters < 100:
        raise SystemExit("--chapters must be at least 100")
    if not 1 <= args.chapter <= args.chapters:
        raise SystemExit("--chapter must be within the fixture chapter range")

    details = seed(args.root.resolve(), args.chapters)
    database = Database(str(details["database"]))
    repository = StoryRepository(database)
    chapter_id = f"fixture-chapter-{args.chapter:04d}"
    first = repository.append_chapter_version(
        str(details["bookId"]),
        args.chapter,
        "Baseline version before the recorded edit.",
        expected_version=0,
    )
    commit_id = repository.create_story_commit(
        chapter_id,
        facts=[{
            "fact_type": "fixture_reveal",
            "content": "The recorded baseline reveal is known to the cast.",
            "entities": ["fixture-character-01"],
        }],
        state_changes={"fixture_reveal_known": True},
        chapter_version_id=str(first["version_id"]),
    )
    repository.accept_story_commit(commit_id)
    second = repository.append_chapter_version(
        str(details["bookId"]),
        args.chapter,
        "Edited version changes the recorded reveal and makes downstream state stale.",
        expected_version=1,
    )
    revised_commit_id = repository.create_story_commit(
        chapter_id,
        facts=[{
            "fact_type": "fixture_reveal",
            "content": "The revised reveal is known after the edit is accepted.",
            "entities": ["fixture-character-01"],
        }],
        state_changes={"fixture_reveal_known": False, "fixture_reveal_revised": True},
        chapter_version_id=str(second["version_id"]),
    )
    repository.accept_story_commit(revised_commit_id)
    details.update({
        "editImpactChapterId": chapter_id,
        "editImpactVersionId": second["version_id"],
        "supersededCommitId": commit_id,
        "revisedCommitId": revised_commit_id,
    })
    print(json.dumps(details, ensure_ascii=False))


if __name__ == "__main__":
    main()
