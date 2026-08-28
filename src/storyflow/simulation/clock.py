"""Deterministic simulation clock rules.

The clock is part of the counterfactual sandbox, never wall-clock state.  A
run may configure an ISO-8601 start time and a round duration; otherwise the
stable defaults make replay and recovery independent of process start time.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any, Mapping

from .models import SimulationRun


DEFAULT_START_TIME = datetime(2000, 1, 1, tzinfo=timezone.utc)
DEFAULT_ROUND_DURATION = timedelta(days=1)
_DURATION_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)\s*(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d|weeks?|w)$", re.IGNORECASE)
_ISO_DURATION_RE = re.compile(r"^P(?:(?P<days>[0-9]+(?:\.[0-9]+)?)D)?(?:T(?:(?P<hours>[0-9]+(?:\.[0-9]+)?)H)?(?:(?P<minutes>[0-9]+(?:\.[0-9]+)?)M)?(?:(?P<seconds>[0-9]+(?:\.[0-9]+)?)S)?)?$", re.IGNORECASE)


def _clock_configuration(run: SimulationRun) -> Mapping[str, Any]:
    value = run.configuration.get("clock") if isinstance(run.configuration, Mapping) else None
    return value if isinstance(value, Mapping) else {}


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"invalid simulation clock startTime: {value}") from exc
    else:
        raise ValueError(f"invalid simulation clock startTime: {value!r}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_duration(value: Any) -> timedelta:
    if isinstance(value, timedelta):
        duration = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        duration = timedelta(seconds=float(value))
    elif isinstance(value, str):
        text = value.strip()
        match = _DURATION_RE.fullmatch(text)
        if not match:
            bare_unit = text.lower()
            if bare_unit in {"second", "seconds", "sec", "secs", "s"}:
                match = _DURATION_RE.fullmatch(f"1 {bare_unit}")
            elif bare_unit in {"minute", "minutes", "min", "mins", "m"}:
                match = _DURATION_RE.fullmatch(f"1 {bare_unit}")
            elif bare_unit in {"hour", "hours", "hr", "hrs", "h"}:
                match = _DURATION_RE.fullmatch(f"1 {bare_unit}")
            elif bare_unit in {"day", "days", "d"}:
                match = _DURATION_RE.fullmatch(f"1 {bare_unit}")
            elif bare_unit in {"week", "weeks", "w"}:
                match = _DURATION_RE.fullmatch(f"1 {bare_unit}")
        if match:
            amount = float(match.group(1))
            unit = match.group(2).lower()
            if unit.startswith("s"):
                duration = timedelta(seconds=amount)
            elif unit.startswith("m"):
                duration = timedelta(minutes=amount)
            elif unit.startswith("h"):
                duration = timedelta(hours=amount)
            elif unit.startswith("w"):
                duration = timedelta(weeks=amount)
            else:
                duration = timedelta(days=amount)
        else:
            iso = _ISO_DURATION_RE.fullmatch(text)
            if not iso or not any(iso.groupdict().values()):
                raise ValueError(f"invalid simulation round duration: {value}")
            duration = timedelta(
                days=float(iso.group("days") or 0),
                hours=float(iso.group("hours") or 0),
                minutes=float(iso.group("minutes") or 0),
                seconds=float(iso.group("seconds") or 0),
            )
    else:
        raise ValueError(f"invalid simulation round duration: {value!r}")
    if duration <= timedelta(0):
        raise ValueError("simulation round duration must be positive")
    return duration


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class SimulationClock:
    """Pure clock calculations for a persisted :class:`SimulationRun`."""

    @staticmethod
    def start_time(run: SimulationRun) -> datetime:
        config = _clock_configuration(run)
        value = config.get("startTime", config.get("start_time", DEFAULT_START_TIME))
        return _parse_datetime(value)

    @staticmethod
    def round_duration(run: SimulationRun) -> timedelta:
        config = _clock_configuration(run)
        value = config.get("roundDuration", config.get("round_duration", config.get("step", DEFAULT_ROUND_DURATION)))
        return _parse_duration(value)

    @classmethod
    def time_for_round(cls, run: SimulationRun, round_number: int) -> str:
        if round_number < 0:
            raise ValueError("simulation round number must be non-negative")
        if run.simulation_time:
            current = _parse_datetime(run.simulation_time)
            delta_rounds = round_number - run.current_round
            if delta_rounds <= 0:
                return _format_datetime(current)
            return _format_datetime(current + cls.round_duration(run) * delta_rounds)
        return cls.time_from_start(run, round_number)

    @classmethod
    def time_from_start(cls, run: SimulationRun, round_number: int) -> str:
        if round_number < 0:
            raise ValueError("simulation round number must be non-negative")
        return _format_datetime(cls.start_time(run) + cls.round_duration(run) * round_number)

    @classmethod
    def initial_time(cls, run: SimulationRun) -> str:
        return cls.time_from_start(run, 0)

    @staticmethod
    def parse(value: str) -> datetime:
        """Parse a persisted clock value for ordering/assertion code."""
        return _parse_datetime(value)
