"""Structured artifact writing for issue attempts."""

from __future__ import annotations

import difflib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pi_sonar_agent.agent.claude_agent import FixResult, SonarIssue
from pi_sonar_agent.core.quality_gate import (
    build_compliance_summary,
    load_default_quality_gate_catalog,
)
from pi_sonar_agent.core.retry_context import RetryContext
from pi_sonar_agent.core.state import (
    AttemptState,
    IssueState,
    RunState,
    TargetState,
    WorkspaceBaseline,
    serialize_state,
)


@dataclass(frozen=True)
class AttemptArtifactBundle:
    """Paths produced for a single issue attempt."""

    issue_root: Path
    attempt_root: Path
    issue_json: Path
    edit_contract_json: Path
    prompt_context_json: Path
    patch_diff: Path
    attempt_events_jsonl: Path
    reviewer_result_json: Path
    build_result_json: Path
    compliance_summary_json: Path
    attempt_summary_json: Path


class ArtifactWriter:
    """Write structured issue attempt artifacts under a stable directory tree."""

    def __init__(
        self,
        root: str | Path = "logs/issue_artifacts",
        run_root: str | Path = "logs/run_artifacts",
    ) -> None:
        self.root = Path(root)
        self.run_root = Path(run_root)

    def write_attempt_artifacts(
        self,
        *,
        repository: str,
        run_label: str,
        issue: SonarIssue,
        attempt_state: AttemptState,
        result: FixResult,
        workspace_path: Path,
        baseline: WorkspaceBaseline,
        build_command: str,
        retry_feedback: str,
        retry_context: RetryContext | None = None,
    ) -> AttemptArtifactBundle:
        """Write the standard artifact bundle for a single attempt."""

        issue_root = self._issue_root(repository, run_label, issue.key)
        attempt_root = issue_root / f"attempt-{attempt_state.attempt_number:02d}"
        attempt_root.mkdir(parents=True, exist_ok=True)

        bundle = AttemptArtifactBundle(
            issue_root=issue_root,
            attempt_root=attempt_root,
            issue_json=attempt_root / "issue.json",
            edit_contract_json=attempt_root / "edit_contract.json",
            prompt_context_json=attempt_root / "prompt_context.json",
            patch_diff=attempt_root / "patch.diff",
            attempt_events_jsonl=attempt_root / "attempt_events.jsonl",
            reviewer_result_json=attempt_root / "reviewer_result.json",
            build_result_json=attempt_root / "build_result.json",
            compliance_summary_json=attempt_root / "compliance_summary.json",
            attempt_summary_json=attempt_root / "attempt_summary.json",
        )

        active_quality_gate_rules = tuple(
            rule.to_dict()
            for rule in getattr(getattr(result, "edit_contract", None), "quality_gate_rules", ())
        )
        compliance_summary = build_compliance_summary(
            getattr(getattr(result, "edit_contract", None), "quality_gate_rules", ()),
            getattr(result, "quality_gate_result", None),
            source_path=load_default_quality_gate_catalog().source_path,
        )

        self._write_json(bundle.issue_json, _issue_payload(issue))
        self._write_json(
            bundle.edit_contract_json,
            _serialize_optional_payload(
                getattr(result, "edit_contract", None),
                {
                    "status": "not_available",
                    "reason": "Edit contract was not attached to this attempt.",
                },
            ),
        )
        self._write_json(
            bundle.prompt_context_json,
            {
                "attempt_number": attempt_state.attempt_number,
                "build_command": build_command,
                "retry_feedback": retry_feedback,
                "retry_context": retry_context.to_dict() if retry_context else None,
                "workspace_path": workspace_path.as_posix(),
                "baseline_head_commit": baseline.head_commit,
                "guardrail_mode": getattr(result, "guardrail_mode", ""),
                "active_quality_gate_rules": active_quality_gate_rules,
                "execution_profile": getattr(result, "execution_profile", "full_path"),
                "fast_path_enabled": bool(getattr(result, "fast_path_enabled", False)),
                "plan_first_enabled": bool(getattr(getattr(result, "edit_contract", None), "plan_first_enabled", False)),
                "rollout_flags": list(getattr(result, "rollout_flags", ())),
                "repair_plan": _serialize_optional_payload(getattr(result, "repair_plan", None), None),
                "plan_precheck": _serialize_optional_payload(getattr(result, "plan_precheck", None), None),
                "planner_lessons": [
                    lesson.to_dict()
                    for lesson in getattr(getattr(result, "edit_contract", None), "planner_lessons", ())
                ],
                "prefetched_context": [
                    snippet.to_dict()
                    for snippet in getattr(getattr(result, "edit_contract", None), "prefetched_context", ())
                ],
            },
        )
        bundle.patch_diff.write_text(
            self._build_patch_diff(workspace_path, baseline, attempt_state.changed_files),
            encoding="utf-8",
        )
        self._write_jsonl(
            bundle.attempt_events_jsonl,
            [
                event.to_dict() if hasattr(event, "to_dict") else serialize_state(event)
                for event in getattr(result, "attempt_events", ())
            ],
        )
        self._write_json(
            bundle.reviewer_result_json,
            _serialize_optional_payload(
                getattr(result, "reviewer_result", None),
                {
                    "status": "not_available",
                    "reason": "Reviewer result was not attached to this attempt.",
                },
            ),
        )
        self._write_json(
            bundle.build_result_json,
            {
                "success": result.success,
                "build_passed": result.build_passed,
                "build_verification_failed": result.build_verification_failed,
                "retryable_failure": result.retryable_failure,
                "failure_kind": result.failure_kind,
                "error": result.error or "",
                "skip_reason": result.skip_reason,
                "build_command": result.build_command or build_command,
                "build_output": result.build_output,
                "guardrail_mode": getattr(result, "guardrail_mode", ""),
                "quality_gate_result": _serialize_optional_payload(
                    getattr(result, "quality_gate_result", None),
                    {
                        "status": "not_available",
                        "reason": "Quality gate result was not attached to this attempt.",
                    },
                ),
                "boundary_failure_code": getattr(result, "boundary_failure_code", ""),
                "boundary_failure_summary": getattr(result, "boundary_failure_summary", ""),
                "secondary_boundary_failure_codes": list(
                    getattr(result, "secondary_boundary_failure_codes", ())
                ),
                "performance_metrics": dict(getattr(result, "performance_metrics", {}) or {}),
                "model_timeout_stage": getattr(result, "model_timeout_stage", ""),
                "patch_salvaged": bool(getattr(result, "patch_salvaged", False)),
                "compliance_summary": compliance_summary.to_dict(),
                "follow_up_log_path": getattr(result, "follow_up_log_path", ""),
                "repair_plan": _serialize_optional_payload(getattr(result, "repair_plan", None), None),
                "plan_precheck": _serialize_optional_payload(getattr(result, "plan_precheck", None), None),
            },
        )
        self._write_json(bundle.compliance_summary_json, compliance_summary.to_dict())
        self._write_json(bundle.attempt_summary_json, attempt_state.to_dict())
        return bundle

    def write_issue_state(
        self,
        issue_state: IssueState,
        *,
        compliance_summary: dict[str, Any] | None = None,
    ) -> Path:
        """Write the final issue state summary."""

        issue_root = Path(issue_state.artifact_root)
        issue_root.mkdir(parents=True, exist_ok=True)
        issue_summary_path = issue_root / "issue_summary.json"
        self._write_json(issue_summary_path, issue_state.to_dict())
        if compliance_summary is not None:
            self._write_json(issue_root / "compliance_summary.json", compliance_summary)
        return issue_summary_path

    def write_target_state(self, target_state: TargetState) -> Path:
        """Write the final target state summary."""

        target_root = self._target_root(
            target_state.run_label,
            target_state.repository,
            target_state.author,
        )
        target_root.mkdir(parents=True, exist_ok=True)
        target_summary_path = target_root / "target_summary.json"
        self._write_json(target_summary_path, target_state.to_dict())
        return target_summary_path

    def write_run_state(self, run_state: RunState) -> Path:
        """Write the final run state summary."""

        run_root = self.run_root / _sanitize_name(run_state.run_label)
        run_root.mkdir(parents=True, exist_ok=True)
        run_summary_path = run_root / "run_summary.json"
        self._write_json(run_summary_path, run_state.to_dict())
        return run_summary_path

    def _issue_root(self, repository: str, run_label: str, issue_key: str) -> Path:
        return (
            self.root
            / _sanitize_name(repository)
            / _sanitize_name(run_label)
            / _sanitize_name(issue_key)
        )

    def _target_root(self, run_label: str, repository: str, author: str) -> Path:
        return (
            self.run_root
            / _sanitize_name(run_label)
            / "targets"
            / f"{_sanitize_name(repository)}__{_sanitize_name(author)}"
        )

    def _build_patch_diff(
        self,
        workspace_path: Path,
        baseline: WorkspaceBaseline,
        changed_files: tuple[str, ...],
    ) -> str:
        """Build a text diff for the files touched by the current attempt."""

        normalized_files = sorted({
            _normalize_rel_path(path)
            for path in changed_files
            if _normalize_rel_path(path)
        })
        if not normalized_files:
            return ""

        hunks: list[str] = []
        for rel_path in normalized_files:
            before_exists, before_text = self._read_baseline_text(workspace_path, baseline, rel_path)
            current_file = workspace_path / rel_path
            after_exists = current_file.is_file()
            after_text = (
                current_file.read_text(encoding="utf-8", errors="replace")
                if after_exists
                else ""
            )
            if not before_exists and not after_exists:
                continue

            diff_lines = difflib.unified_diff(
                before_text.splitlines(keepends=True) if before_exists else [],
                after_text.splitlines(keepends=True) if after_exists else [],
                fromfile=(f"a/{rel_path}" if before_exists else "/dev/null"),
                tofile=(f"b/{rel_path}" if after_exists else "/dev/null"),
                lineterm="",
            )
            rendered = "\n".join(diff_lines).strip()
            if rendered:
                hunks.append(rendered)

        return "\n\n".join(hunks).rstrip() + ("\n" if hunks else "")

    def _read_baseline_text(
        self,
        workspace_path: Path,
        baseline: WorkspaceBaseline,
        rel_path: str,
    ) -> tuple[bool, str]:
        """Read file content from the captured baseline."""

        if rel_path in baseline.untracked_files:
            path = baseline.untracked_root / rel_path
            if path.is_file():
                return True, path.read_text(encoding="utf-8", errors="replace")

        if rel_path in baseline.tracked_files:
            path = baseline.tracked_root / rel_path
            if path.is_file():
                return True, path.read_text(encoding="utf-8", errors="replace")

        result = subprocess.run(
            ["git", "show", f"{baseline.head_commit}:{rel_path}"],
            cwd=str(workspace_path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            return False, ""
        return True, result.stdout

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(serialize_state(payload), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _write_jsonl(path: Path, payload: list[dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for item in payload:
                handle.write(json.dumps(serialize_state(item), ensure_ascii=False) + "\n")


def _issue_payload(issue: SonarIssue) -> dict[str, Any]:
    return {
        "issue_key": issue.key,
        "rule_id": issue.rule,
        "message": issue.message,
        "line": issue.line,
        "component": issue.component,
        "file_path": issue.file_path,
        "severity": issue.severity,
        "issue_type": issue.issue_type,
        "status": issue.status,
    }


def _serialize_optional_payload(value: Any, fallback: dict[str, Any]) -> dict[str, Any]:
    if value is None:
        return fallback
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        payload = to_dict()
        if isinstance(payload, dict):
            return payload
    if isinstance(value, dict):
        return value
    return fallback


def _sanitize_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-") or "item"


def _normalize_rel_path(path: str) -> str:
    return str(path or "").replace("\\", "/").lstrip("/")
