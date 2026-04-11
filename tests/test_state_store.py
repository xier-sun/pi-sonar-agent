from __future__ import annotations

import json
from pathlib import Path

from pi_sonar_agent.core.events import AttemptEvent, EventKind, EventRecorder, StateEvent
from pi_sonar_agent.core.state import (
    AttemptState,
    AttemptStatus,
    IssueState,
    IssueStatus,
    RetryReason,
    RunState,
    RunStatus,
    TargetState,
    TargetStatus,
)
from pi_sonar_agent.core.state_store import RunStateStore


class _FakeDbClient:
    def __init__(self) -> None:
        self.ensure_tables_calls = 0
        self.run_records: list[tuple[str, str, str, int]] = []
        self.issue_records: list[tuple[int, str, str, str, int]] = []
        self.updated_issue_records: list[tuple[str, str, str | None, str | None]] = []
        self.updated_run_records: list[tuple[int, int | None, int | None, str | None, str | None, str | None]] = []
        self.snapshots: list[dict] = []
        self.events: list[dict] = []

    def ensure_tables(self) -> None:
        self.ensure_tables_calls += 1

    def insert_run_record(self, author: str, project_key: str, repository: str, total_issues: int) -> int:
        self.run_records.append((author, project_key, repository, total_issues))
        return 7

    def update_run_record(
        self,
        run_id: int,
        successful_fixes: int = None,
        failed_fixes: int = None,
        status: str = None,
        error: str = None,
        pr_url: str = None,
    ) -> None:
        self.updated_run_records.append((run_id, successful_fixes, failed_fixes, status, error, pr_url))

    def insert_issue_record(
        self,
        run_id: int,
        issue_key: str,
        rule_id: str,
        file_path: str,
        line_number: int,
    ) -> None:
        self.issue_records.append((run_id, issue_key, rule_id, file_path, line_number))

    def update_issue_record(
        self,
        issue_key: str,
        fix_status: str,
        fix_engine: str = None,
        error_message: str = None,
    ) -> None:
        self.updated_issue_records.append((issue_key, fix_status, fix_engine, error_message))

    def upsert_state_snapshot(self, **kwargs) -> None:
        self.snapshots.append(kwargs)

    def insert_event_record(self, **kwargs) -> None:
        self.events.append(kwargs)

    def disconnect(self) -> None:
        return None


class _FailingDbClient:
    def ensure_tables(self) -> None:
        raise RuntimeError("db offline")

    def disconnect(self) -> None:
        return None


def test_run_state_store_persists_events_and_snapshots_with_db(tmp_path: Path) -> None:
    recorder = EventRecorder(root=tmp_path / "run-artifacts")
    db_client = _FakeDbClient()
    store = RunStateStore(db_client=db_client, event_recorder=recorder)

    attempt_state = AttemptState(
        attempt_number=1,
        status=AttemptStatus.SUCCEEDED,
        started_at="2026-04-03T10:00:00+00:00",
        finished_at="2026-04-03T10:00:10+00:00",
        duration_seconds=10.0,
        retry_reason=RetryReason.NONE,
        summary="Fixed 1 file(s)",
        artifact_dir=(tmp_path / "issue-artifacts" / "attempt-01").as_posix(),
    )
    issue_state = IssueState(
        issue_key="ISSUE-1",
        repository="repo-a",
        run_label="run-1",
        rule_id="csharpsquid:S1481",
        file_path="/src/Foo.cs",
        line=12,
        status=IssueStatus.FIXED,
        attempts=(attempt_state,),
        artifact_root=(tmp_path / "issue-artifacts" / "repo-a" / "run-1" / "ISSUE-1").as_posix(),
    )
    target_state = TargetState(
        run_label="run-1",
        project_key="project-a",
        repository="repo-a",
        author="alice@example.com",
        base_branch="main",
        status=TargetStatus.SUCCEEDED,
        issues=(issue_state,),
        started_at="2026-04-03T10:00:00+00:00",
        finished_at="2026-04-03T10:10:00+00:00",
    )
    run_state = RunState(
        run_label="run-1",
        status=RunStatus.SUCCEEDED,
        targets=(target_state,),
        started_at="2026-04-03T10:00:00+00:00",
        finished_at="2026-04-03T10:10:00+00:00",
    )

    store.record_event(
        StateEvent(
            kind=EventKind.RUN_STARTED,
            run_label="run-1",
            entity_type="run",
            entity_key="run-1",
            status="running",
            payload={"mode": "single"},
        )
    )
    store.record_target_started(
        run_label="run-1",
        project_key="project-a",
        repository="repo-a",
        author="alice@example.com",
        base_branch="main",
        total_issues=1,
    )
    store.record_issue_started(
        run_label="run-1",
        repository="repo-a",
        author="alice@example.com",
        project_key="project-a",
        issue_key="ISSUE-1",
        rule_id="csharpsquid:S1481",
        file_path="/src/Foo.cs",
        line_number=12,
    )
    store.record_attempt_started(
        run_label="run-1",
        repository="repo-a",
        author="alice@example.com",
        project_key="project-a",
        issue_key="ISSUE-1",
        attempt_number=1,
        build_command='dotnet build "Foo.sln"',
        retry_context={"failure_kind": "build"},
    )
    store.record_attempt_state(
        attempt_state,
        run_label="run-1",
        repository="repo-a",
        author="alice@example.com",
        project_key="project-a",
        issue_key="ISSUE-1",
    )
    store.record_issue_state(issue_state, author="alice@example.com", project_key="project-a")
    store.record_target_state(
        target_state,
        successful=1,
        skipped=0,
        failed=0,
        build_passed=True,
        pr_url="https://ado/pr/1",
        artifact_path=(tmp_path / "run-artifacts" / "run-1" / "targets" / "repo-a__alice_example.com" / "target_summary.json").as_posix(),
    )
    store.record_run_state(
        run_state,
        artifact_path=(tmp_path / "run-artifacts" / "run-1" / "run_summary.json").as_posix(),
    )
    event_log_path = store.record_event(
        StateEvent(
            kind=EventKind.RUN_FINISHED,
            run_label="run-1",
            entity_type="run",
            entity_key="run-1",
            status="succeeded",
            payload=run_state.to_dict(),
        )
    )

    assert db_client.ensure_tables_calls == 1
    assert db_client.run_records == [("alice@example.com", "project-a", "repo-a", 1)]
    assert db_client.issue_records == [(7, "ISSUE-1", "csharpsquid:S1481", "/src/Foo.cs", 12)]
    assert db_client.updated_issue_records[0][0] == "ISSUE-1"
    assert db_client.updated_run_records[0][0] == 7
    assert any(snapshot["entity_type"] == "attempt" for snapshot in db_client.snapshots)
    assert any(snapshot["entity_type"] == "issue" for snapshot in db_client.snapshots)
    assert any(snapshot["entity_type"] == "target" for snapshot in db_client.snapshots)
    assert any(snapshot["entity_type"] == "run" for snapshot in db_client.snapshots)
    assert any(event["event_kind"] == "attempt_finished" for event in db_client.events)
    assert event_log_path.exists()
    lines = event_log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 6
    assert json.loads(lines[0])["kind"] == "run_started"


def test_run_state_store_disables_db_but_keeps_event_artifacts(tmp_path: Path) -> None:
    recorder = EventRecorder(root=tmp_path / "run-artifacts")
    store = RunStateStore(db_client=_FailingDbClient(), event_recorder=recorder)

    event_log_path = store.record_event(
        AttemptEvent(
            kind=EventKind.ATTEMPT_STARTED,
            run_label="run-fallback",
            entity_type="attempt",
            entity_key="ISSUE-1::attempt-01",
            repository="repo-a",
            issue_key="ISSUE-1",
            attempt_number=1,
            status="running",
            payload={"build_command": 'dotnet build "Foo.sln"'},
        )
    )

    assert event_log_path.exists()
    assert store.last_db_error == "db offline"


def test_run_state_store_logs_when_db_is_disabled(tmp_path: Path, capsys) -> None:
    recorder = EventRecorder(root=tmp_path / "run-artifacts")
    store = RunStateStore(db_client=_FailingDbClient(), event_recorder=recorder)

    store.record_event(
        StateEvent(
            kind=EventKind.RUN_STARTED,
            run_label="run-db-warning",
            entity_type="run",
            entity_key="run-db-warning",
            status="running",
            payload={"mode": "single"},
        )
    )

    captured = capsys.readouterr()
    assert "StateStore DB sync disabled: db offline" in captured.out


def test_run_state_store_records_abort_events(tmp_path: Path) -> None:
    recorder = EventRecorder(root=tmp_path / "run-artifacts")
    store = RunStateStore(event_recorder=recorder)

    store.record_run_aborted(
        run_label="run-abort",
        status="failed",
        repository="repo-a",
        author="alice@example.com",
        project_key="project-a",
        error="startup exploded",
        startup_failure=True,
        payload={"mode": "single"},
    )
    store.record_target_aborted(
        run_label="run-abort",
        project_key="project-a",
        repository="repo-a",
        author="alice@example.com",
        base_branch="main",
        total_issues=3,
        error="clone failed",
        before_first_issue=True,
        startup_failure=True,
        payload={"scope_audit_mode": "scope_soft_audit"},
    )

    event_log_path = tmp_path / "run-artifacts" / "run-abort" / "events.jsonl"
    lines = [json.loads(line) for line in event_log_path.read_text(encoding="utf-8").splitlines()]

    assert [item["kind"] for item in lines] == ["startup_failure", "target_aborted"]
    assert lines[0]["payload"]["error"] == "startup exploded"
    assert lines[1]["payload"]["before_first_issue"] is True
    assert lines[1]["payload"]["scope_audit_mode"] == "scope_soft_audit"
