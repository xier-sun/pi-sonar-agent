"""Structured lifecycle events for run/target/issue/attempt processing."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from pi_sonar_agent.core.state import serialize_state, utc_now_iso


class EventKind(StrEnum):
    """Lifecycle events emitted during a run."""

    RUN_STARTED = "run_started"
    RUN_FINISHED = "run_finished"
    TARGET_STARTED = "target_started"
    TARGET_FINISHED = "target_finished"
    ISSUE_STARTED = "issue_started"
    ISSUE_FINISHED = "issue_finished"
    ATTEMPT_STARTED = "attempt_started"
    ATTEMPT_FINISHED = "attempt_finished"


@dataclass(frozen=True)
class StateEvent:
    """Structured event for run/target/issue lifecycle transitions."""

    kind: EventKind
    run_label: str
    entity_type: str
    entity_key: str
    repository: str = ""
    author: str = ""
    project_key: str = ""
    issue_key: str = ""
    status: str = ""
    artifact_path: str = ""
    timestamp: str = field(default_factory=utc_now_iso)
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the event to a JSON-ready dictionary."""

        return serialize_state(self)


@dataclass(frozen=True)
class AttemptEvent(StateEvent):
    """Structured event for a single issue attempt."""

    attempt_number: int = 0


class EventRecorder:
    """Append structured lifecycle events to a per-run JSONL log."""

    def __init__(self, root: str | Path = "logs/run_artifacts") -> None:
        self.root = Path(root)

    def record(self, event: StateEvent | AttemptEvent) -> Path:
        """Append one event to the JSONL stream and return the log path."""

        path = self._event_log_path(event.run_label)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        return path

    def _event_log_path(self, run_label: str) -> Path:
        return self.root / _sanitize_name(run_label) / "events.jsonl"


def build_target_entity_key(repository: str, author: str) -> str:
    """Build a stable entity key for a target."""

    return f"{repository}::{author}"


def build_attempt_entity_key(issue_key: str, attempt_number: int) -> str:
    """Build a stable entity key for an issue attempt."""

    return f"{issue_key}::attempt-{attempt_number:02d}"


def _sanitize_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-") or "item"
