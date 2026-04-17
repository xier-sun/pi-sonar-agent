"""Persistence for issue-level working memory artifacts."""

from __future__ import annotations

import json
import re
from pathlib import Path

from pi_sonar_agent.core.memory.child_agent_memory import ChildAgentMemory
from pi_sonar_agent.core.memory.evidence_state import EvidenceState
from pi_sonar_agent.core.memory.issue_working_memory import IssueWorkingMemory
from pi_sonar_agent.core.memory.memory_schema import ensure_dict


def _sanitize_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-") or "issue"


class WorkingMemoryStore:
    """Load and save canonical working-memory state under the workspace runtime root."""

    def __init__(self, workspace_path: Path) -> None:
        self.workspace_path = Path(workspace_path)
        git_dir = self.workspace_path / ".git"
        if git_dir.is_dir():
            self.root = git_dir / "pi-sonar-agent-runtime" / "issues"
        else:
            self.root = self.workspace_path / ".pi-sonar-agent-runtime" / "issues"
        self.root.mkdir(parents=True, exist_ok=True)

    def issue_root(self, issue_key: str) -> Path:
        root = self.root / _sanitize_name(issue_key)
        root.mkdir(parents=True, exist_ok=True)
        return root

    def working_memory_path(self, issue_key: str) -> Path:
        return self.issue_root(issue_key) / "working-memory.json"

    def evidence_index_path(self, issue_key: str) -> Path:
        return self.issue_root(issue_key) / "evidence-index.json"

    def compact_summary_path(self, issue_key: str) -> Path:
        return self.issue_root(issue_key) / "compact-summary.md"

    def child_memory_path(self, issue_key: str, role: str) -> Path:
        role_name = _sanitize_name(role)
        return self.issue_root(issue_key) / f"child-memory-{role_name}.json"

    def load(self, issue_key: str) -> IssueWorkingMemory | None:
        path = self.working_memory_path(issue_key)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return IssueWorkingMemory.from_dict(payload)

    def save(self, memory: IssueWorkingMemory) -> Path:
        validated = IssueWorkingMemory.from_dict(memory.to_dict())
        path = self.working_memory_path(validated.issue_key)
        path.write_text(
            json.dumps(validated.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def load_child_memory(self, issue_key: str, role: str) -> ChildAgentMemory | None:
        path = self.child_memory_path(issue_key, role)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ChildAgentMemory.from_dict(payload)

    def save_child_memory(self, memory: ChildAgentMemory) -> Path:
        validated = ChildAgentMemory.from_dict(memory.to_dict())
        path = self.child_memory_path(validated.issue_key, validated.role)
        path.write_text(
            json.dumps(validated.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def load_evidence(self, issue_key: str) -> tuple[EvidenceState, ...]:
        path = self.evidence_index_path(issue_key)
        if not path.exists():
            return ()
        payload = ensure_dict(
            json.loads(path.read_text(encoding="utf-8")),
            field_name="evidence_index",
        )
        items = payload.get("items", ())
        if not isinstance(items, list):
            return ()
        return tuple(
            EvidenceState.from_dict(item)
            for item in items
            if isinstance(item, dict)
        )

    def save_evidence(
        self,
        issue_key: str,
        evidence_items: tuple[EvidenceState, ...] | list[EvidenceState],
    ) -> Path:
        normalized = tuple(
            EvidenceState.from_dict(item.to_dict() if hasattr(item, "to_dict") else dict(item))
            for item in evidence_items
        )
        path = self.evidence_index_path(issue_key)
        payload = {
            "version": 1,
            "issue_key": str(issue_key or "").strip(),
            "items": [item.to_dict() for item in normalized],
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def save_compact_summary(self, issue_key: str, content: str) -> Path:
        path = self.compact_summary_path(issue_key)
        path.write_text(str(content or "").rstrip() + "\n", encoding="utf-8")
        return path
