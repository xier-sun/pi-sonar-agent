"""Persistent queue for incidental follow-up findings."""

from __future__ import annotations

import json
import re
from pathlib import Path

from pi_sonar_agent.core.diff_reviewer import FollowUpItem
from pi_sonar_agent.core.state import serialize_state


def _sanitize_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-") or "item"


class FollowUpStore:
    """Append reviewer follow-ups to a stable JSONL queue."""

    def __init__(self, root: str | Path = "logs/follow_ups") -> None:
        self.root = Path(root)

    def append(
        self,
        *,
        repository: str,
        run_label: str,
        issue_key: str,
        follow_ups: tuple[FollowUpItem, ...] | list[FollowUpItem],
    ) -> Path | None:
        """Append follow-ups for one issue attempt and return the queue path."""

        if not follow_ups:
            return None

        queue_path = (
            self.root
            / _sanitize_name(repository)
            / _sanitize_name(run_label)
            / f"{_sanitize_name(issue_key)}.jsonl"
        )
        queue_path.parent.mkdir(parents=True, exist_ok=True)
        with queue_path.open("a", encoding="utf-8") as handle:
            for item in follow_ups:
                handle.write(json.dumps(serialize_state(item), ensure_ascii=False) + "\n")
        return queue_path
