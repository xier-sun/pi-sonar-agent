"""State persistence layer with artifact-first fallback and optional DB sync."""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path
from typing import Any

from pi_sonar_agent.core.db_client import MySQLClient
from pi_sonar_agent.core.events import (
    AttemptEvent,
    EventKind,
    EventRecorder,
    StateEvent,
    build_attempt_entity_key,
    build_target_entity_key,
)
from pi_sonar_agent.core.state import AttemptState, IssueState, RunState, TargetState


class RunStateStore:
    """Persist run lifecycle state to artifacts and, when available, to MySQL."""

    def __init__(
        self,
        *,
        db_client: MySQLClient | None = None,
        event_recorder: EventRecorder | None = None,
    ) -> None:
        self.db_client = db_client
        self.event_recorder = event_recorder or EventRecorder()
        self._db_enabled = db_client is not None
        self._db_initialized = False
        self._run_ids: dict[tuple[str, str, str], int] = {}
        self.last_db_error: str = ""

    def initialize(self) -> None:
        """Prepare the DB schema when a database client is configured."""

        if not self._db_enabled or self._db_initialized or self.db_client is None:
            return
        try:
            self.db_client.ensure_tables()
            self._db_initialized = True
        except Exception as exc:
            self._disable_db(exc)

    def record_event(self, event: StateEvent | AttemptEvent) -> Path:
        """Persist one lifecycle event to artifact log and optional DB."""

        path = self.event_recorder.record(event)
        self.initialize()
        if self._db_enabled and self.db_client is not None:
            try:
                self.db_client.insert_event_record(
                    run_label=event.run_label,
                    event_kind=event.kind.value,
                    entity_type=event.entity_type,
                    entity_key=event.entity_key,
                    repository=event.repository,
                    author=event.author,
                    project_key=event.project_key,
                    issue_key=event.issue_key,
                    attempt_number=getattr(event, "attempt_number", 0),
                    status=event.status,
                    artifact_path=path.as_posix(),
                    payload=event.to_dict(),
                )
            except Exception as exc:
                self._disable_db(exc)
        return path

    def record_run_state(self, run_state: RunState, *, artifact_path: str = "") -> None:
        """Persist a run summary snapshot."""

        self.initialize()
        if not self._db_enabled or self.db_client is None:
            return
        try:
            self.db_client.upsert_state_snapshot(
                run_label=run_state.run_label,
                entity_type="run",
                entity_key=run_state.run_label,
                status=run_state.status.value,
                artifact_path=artifact_path,
                payload=run_state.to_dict(),
            )
        except Exception as exc:
            self._disable_db(exc)

    def record_target_started(
        self,
        *,
        run_label: str,
        project_key: str,
        repository: str,
        author: str,
        base_branch: str,
        total_issues: int,
    ) -> None:
        """Persist the start of one target run."""

        event = StateEvent(
            kind=EventKind.TARGET_STARTED,
            run_label=run_label,
            entity_type="target",
            entity_key=build_target_entity_key(repository, author),
            repository=repository,
            author=author,
            project_key=project_key,
            status="running",
            payload={
                "project_key": project_key,
                "repository": repository,
                "author": author,
                "base_branch": base_branch,
                "total_issues": total_issues,
            },
        )
        self.record_event(event)
        self.initialize()
        if not self._db_enabled or self.db_client is None:
            return
        try:
            run_id = self.db_client.insert_run_record(
                author=author,
                project_key=project_key,
                repository=repository,
                total_issues=total_issues,
            )
            self._run_ids[(run_label, repository, author)] = run_id
        except Exception as exc:
            self._disable_db(exc)

    def record_target_state(
        self,
        target_state: TargetState,
        *,
        successful: int,
        skipped: int,
        failed: int,
        build_passed: bool,
        pr_url: str = "",
        pr_error: str = "",
        artifact_path: str = "",
    ) -> None:
        """Persist one target summary and compatibility updates."""

        self.initialize()
        if self._db_enabled and self.db_client is not None:
            try:
                self.db_client.upsert_state_snapshot(
                    run_label=target_state.run_label,
                    entity_type="target",
                    entity_key=build_target_entity_key(target_state.repository, target_state.author),
                    repository=target_state.repository,
                    author=target_state.author,
                    project_key=target_state.project_key,
                    status=target_state.status.value,
                    artifact_path=artifact_path,
                    payload=target_state.to_dict(),
                )
                run_id = self._run_ids.get(
                    (target_state.run_label, target_state.repository, target_state.author)
                )
                if run_id is not None:
                    self.db_client.update_run_record(
                        run_id,
                        successful_fixes=successful,
                        failed_fixes=failed + skipped,
                        status=target_state.status.value,
                        error=pr_error or None,
                        pr_url=pr_url or None,
                    )
            except Exception as exc:
                self._disable_db(exc)

        self.record_event(
            StateEvent(
                kind=EventKind.TARGET_FINISHED,
                run_label=target_state.run_label,
                entity_type="target",
                entity_key=build_target_entity_key(target_state.repository, target_state.author),
                repository=target_state.repository,
                author=target_state.author,
                project_key=target_state.project_key,
                status=target_state.status.value,
                artifact_path=artifact_path,
                payload={
                    "successful": successful,
                    "skipped": skipped,
                    "failed": failed,
                    "build_passed": build_passed,
                    "pr_url": pr_url,
                    "pr_error": pr_error,
                },
            )
        )

    def record_issue_started(
        self,
        *,
        run_label: str,
        repository: str,
        author: str,
        project_key: str,
        issue_key: str,
        rule_id: str,
        file_path: str,
        line_number: int,
    ) -> None:
        """Persist the start of issue processing."""

        self.record_event(
            StateEvent(
                kind=EventKind.ISSUE_STARTED,
                run_label=run_label,
                entity_type="issue",
                entity_key=issue_key,
                repository=repository,
                author=author,
                project_key=project_key,
                issue_key=issue_key,
                status="running",
                payload={
                    "rule_id": rule_id,
                    "file_path": file_path,
                    "line_number": line_number,
                },
            )
        )
        self.initialize()
        if not self._db_enabled or self.db_client is None:
            return
        try:
            run_id = self._run_ids.get((run_label, repository, author))
            if run_id is not None:
                self.db_client.insert_issue_record(
                    run_id=run_id,
                    issue_key=issue_key,
                    rule_id=rule_id,
                    file_path=file_path,
                    line_number=line_number,
                )
        except Exception as exc:
            self._disable_db(exc)

    def record_attempt_started(
        self,
        *,
        run_label: str,
        repository: str,
        author: str,
        project_key: str,
        issue_key: str,
        attempt_number: int,
        build_command: str,
        retry_context: dict[str, Any] | None,
    ) -> None:
        """Persist the start of one issue attempt."""

        self.record_event(
            AttemptEvent(
                kind=EventKind.ATTEMPT_STARTED,
                run_label=run_label,
                entity_type="attempt",
                entity_key=build_attempt_entity_key(issue_key, attempt_number),
                repository=repository,
                author=author,
                project_key=project_key,
                issue_key=issue_key,
                attempt_number=attempt_number,
                status="running",
                payload={
                    "build_command": build_command,
                    "retry_context": retry_context,
                },
            )
        )

    def record_attempt_state(
        self,
        attempt_state: AttemptState,
        *,
        run_label: str,
        repository: str,
        author: str,
        project_key: str,
        issue_key: str,
    ) -> None:
        """Persist one attempt snapshot and finish event."""

        self.initialize()
        if self._db_enabled and self.db_client is not None:
            try:
                self.db_client.upsert_state_snapshot(
                    run_label=run_label,
                    entity_type="attempt",
                    entity_key=build_attempt_entity_key(issue_key, attempt_state.attempt_number),
                    repository=repository,
                    author=author,
                    project_key=project_key,
                    issue_key=issue_key,
                    attempt_number=attempt_state.attempt_number,
                    status=attempt_state.status.value,
                    artifact_path=attempt_state.artifact_dir,
                    payload=attempt_state.to_dict(),
                )
            except Exception as exc:
                self._disable_db(exc)

        self.record_event(
            AttemptEvent(
                kind=EventKind.ATTEMPT_FINISHED,
                run_label=run_label,
                entity_type="attempt",
                entity_key=build_attempt_entity_key(issue_key, attempt_state.attempt_number),
                repository=repository,
                author=author,
                project_key=project_key,
                issue_key=issue_key,
                attempt_number=attempt_state.attempt_number,
                status=attempt_state.status.value,
                artifact_path=attempt_state.artifact_dir,
                payload=attempt_state.to_dict(),
            )
        )

    def record_issue_state(
        self,
        issue_state: IssueState,
        *,
        author: str,
        project_key: str,
    ) -> None:
        """Persist one issue summary and compatibility updates."""

        self.initialize()
        if self._db_enabled and self.db_client is not None:
            try:
                self.db_client.upsert_state_snapshot(
                    run_label=issue_state.run_label,
                    entity_type="issue",
                    entity_key=issue_state.issue_key,
                    repository=issue_state.repository,
                    author=author,
                    project_key=project_key,
                    issue_key=issue_state.issue_key,
                    status=issue_state.status.value,
                    artifact_path=issue_state.artifact_root,
                    payload=issue_state.to_dict(),
                )
                self.db_client.update_issue_record(
                    issue_key=issue_state.issue_key,
                    fix_status=issue_state.status.value,
                    fix_engine="claude_code_sdk",
                    error_message=issue_state.final_error or issue_state.final_skip_reason or None,
                )
            except Exception as exc:
                self._disable_db(exc)

        self.record_event(
            StateEvent(
                kind=EventKind.ISSUE_FINISHED,
                run_label=issue_state.run_label,
                entity_type="issue",
                entity_key=issue_state.issue_key,
                repository=issue_state.repository,
                author=author,
                project_key=project_key,
                issue_key=issue_state.issue_key,
                status=issue_state.status.value,
                artifact_path=issue_state.artifact_root,
                payload=issue_state.to_dict(),
            )
        )

    def _disable_db(self, exc: Exception) -> None:
        self._db_enabled = False
        self.last_db_error = str(exc)
        if self.db_client is not None:
            with suppress(Exception):
                self.db_client.disconnect()
