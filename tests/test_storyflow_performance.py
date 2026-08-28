"""Executable contract for the deterministic StoryFlow runtime benchmark."""

from scripts.benchmark_storyflow_simulation import DEFAULT_CASES, run_benchmarks


def test_storyflow_benchmark_declares_required_scales_and_records_real_ledger():
    assert DEFAULT_CASES == ((10, 20), (25, 50), (50, 100))
    result = run_benchmarks(((10, 20),))[0]
    assert result["agents"] == 10
    assert result["rounds"] == 20
    assert result["events"] == 200
    assert result["runStatus"] == "COMPLETED"
    assert result["elapsedSeconds"] > 0
    assert result["stateHash"]
    assert result["canonicalMutation"] is False
    assert result["projectionMode"] == "core-ledger-without-rebuildable-read-models"
