from __future__ import annotations

import json
from types import SimpleNamespace

from pi_sonar_agent.core.job_store import JobStore


class _FakeDbClient:
    def __init__(self) -> None:
        self.ensure_tables_calls = 0
        self.inserted_job: dict | None = None
        self.jobs: dict[str, dict] = {}
        self.updated_jobs: list[tuple[str, dict]] = []
        self.claimed_row: dict | None = None
        self.recorded_commands: list[dict] = []
        self.timeout_calls: list[tuple[object, str]] = []
        self.count_run_jobs_result = 0

    def ensure_tables(self) -> None:
        self.ensure_tables_calls += 1

    def insert_run_job(self, **kwargs) -> int:
        self.inserted_job = dict(kwargs)
        row = {
            "id": 1,
            "job_id": kwargs["job_id"],
            "status": kwargs["status"],
            "trigger_source": kwargs["trigger_source"],
            "trigger_user_id": kwargs.get("trigger_user_id", ""),
            "trigger_user_name": kwargs.get("trigger_user_name", ""),
            "conversation_type": kwargs.get("conversation_type", ""),
            "conversation_id": kwargs.get("conversation_id", ""),
            "repository": kwargs["repository"],
            "project_key": kwargs["project_key"],
            "author": kwargs["author"],
            "base_branch": kwargs["base_branch"],
            "issue_keys_json": kwargs.get("issue_keys_json", ""),
            "skip_issue_keys_json": kwargs.get("skip_issue_keys_json", ""),
            "max_issues": kwargs.get("max_issues", 0),
            "reviewer_email": kwargs.get("reviewer_email", ""),
            "dingtalk_userid": kwargs.get("dingtalk_userid", ""),
            "target_payload_json": kwargs.get("target_payload_json", ""),
            "confirmation_token": kwargs.get("confirmation_token", ""),
            "confirmation_card_instance_id": kwargs.get("confirmation_card_instance_id", ""),
            "confirmed_at": kwargs.get("confirmed_at", ""),
            "queued_at": kwargs.get("queued_at", ""),
            "started_at": "",
            "finished_at": "",
            "run_label": "",
            "result_status": "",
            "pr_url": "",
            "target_summary_path": "",
            "run_log_path": "",
            "error_message": "",
            "created_at": "",
            "updated_at": "",
        }
        self.jobs[kwargs["job_id"]] = row
        return 1

    def get_run_job_by_job_id(self, job_id: str):
        row = self.jobs.get(job_id)
        return dict(row) if row else None

    def get_run_job_by_confirmation_token(self, token: str):
        for row in self.jobs.values():
            if row.get("confirmation_token") == token:
                return dict(row)
        return None

    def get_run_job_by_confirmation_card_instance_id(self, card_instance_id: str):
        for row in self.jobs.values():
            if row.get("confirmation_card_instance_id") == card_instance_id:
                return dict(row)
        return None

    def get_latest_run_job_for_user(self, trigger_user_id: str):
        rows = [
            dict(row)
            for row in self.jobs.values()
            if row.get("trigger_user_id", "") == trigger_user_id
        ]
        if not rows:
            return None
        rows.sort(key=lambda row: row["job_id"], reverse=True)
        return rows[0]

    def list_run_jobs(self, *, status: str = "", limit: int = 50):
        rows = list(self.jobs.values())
        if status:
            rows = [row for row in rows if row["status"] == status]
        return [dict(row) for row in rows[:limit]]

    def update_run_job_fields(self, job_id: str, updates: dict) -> int:
        self.updated_jobs.append((job_id, dict(updates)))
        row = self.jobs[job_id]
        row.update(updates)
        return 1

    def claim_next_run_job(self):
        return dict(self.claimed_row) if self.claimed_row else None

    def insert_dingtalk_command_record(self, **kwargs) -> int:
        self.recorded_commands.append(dict(kwargs))
        return 9

    def mark_stale_running_jobs_timed_out(self, *, timeout_before, error_message: str) -> int:
        self.timeout_calls.append((timeout_before, error_message))
        return 3

    def count_run_jobs(self, *, trigger_user_id: str = "", statuses=(), created_after=None) -> int:
        return self.count_run_jobs_result


def test_job_store_creates_queued_job_with_normalized_payload() -> None:
    fake_db = _FakeDbClient()
    store = JobStore(fake_db)

    job = store.create_job(
        repository="BI",
        project_key="sonar-bi",
        author="alice@example.com",
        base_branch="develop",
        issue_keys=("a", "a", "b"),
        skip_issue_keys=("x", "x"),
        max_issues=5,
        reviewer_email="reviewer@example.com",
        dingtalk_userid="ding-user",
        target_payload={"build_command": "dotnet build Foo.sln"},
    )

    assert fake_db.ensure_tables_calls == 1
    assert job.status == "queued"
    assert job.issue_keys == ("a", "b")
    assert job.skip_issue_keys == ("x",)
    assert job.target_payload["build_command"] == "dotnet build Foo.sln"
    assert fake_db.inserted_job is not None
    assert json.loads(fake_db.inserted_job["issue_keys_json"]) == ["a", "b"]
    assert json.loads(fake_db.inserted_job["skip_issue_keys_json"]) == ["x"]
    assert fake_db.inserted_job["queued_at"] is not None


def test_job_store_confirms_awaiting_job() -> None:
    fake_db = _FakeDbClient()
    store = JobStore(fake_db)
    fake_db.jobs["JOB-1"] = {
        "id": 1,
        "job_id": "JOB-1",
        "status": "awaiting_confirmation",
        "trigger_source": "dingtalk_bot",
        "trigger_user_id": "",
        "trigger_user_name": "",
        "conversation_type": "",
        "conversation_id": "",
        "repository": "BI",
        "project_key": "sonar-bi",
        "author": "alice@example.com",
        "base_branch": "develop",
        "issue_keys_json": "[]",
        "skip_issue_keys_json": "[]",
        "max_issues": 0,
        "reviewer_email": "",
        "dingtalk_userid": "",
        "target_payload_json": "{}",
        "confirmation_token": "token-1",
        "confirmation_card_instance_id": "",
        "confirmed_at": "",
        "queued_at": "",
        "started_at": "",
        "finished_at": "",
        "run_label": "",
        "result_status": "",
        "pr_url": "",
        "target_summary_path": "",
        "run_log_path": "",
        "error_message": "",
        "created_at": "",
        "updated_at": "",
    }

    job = store.confirm_job(job_id="JOB-1", confirmation_token="token-1")

    assert job is not None
    assert job.status == "queued"
    assert fake_db.updated_jobs
    assert fake_db.updated_jobs[-1][1]["status"] == "queued"


def test_job_store_confirms_waiting_job_by_job_id() -> None:
    fake_db = _FakeDbClient()
    store = JobStore(fake_db)
    fake_db.jobs["JOB-8"] = {
        "id": 8,
        "job_id": "JOB-8",
        "status": "awaiting_confirmation",
        "trigger_source": "dingtalk_bot",
        "trigger_user_id": "",
        "trigger_user_name": "",
        "conversation_type": "",
        "conversation_id": "",
        "repository": "BI",
        "project_key": "sonar-bi",
        "author": "alice@example.com",
        "base_branch": "develop",
        "issue_keys_json": "[]",
        "skip_issue_keys_json": "[]",
        "max_issues": 0,
        "reviewer_email": "",
        "dingtalk_userid": "",
        "target_payload_json": "{}",
        "confirmation_token": "token-8",
        "confirmation_card_instance_id": "",
        "confirmed_at": "",
        "queued_at": "",
        "started_at": "",
        "finished_at": "",
        "run_label": "",
        "result_status": "",
        "pr_url": "",
        "target_summary_path": "",
        "run_log_path": "",
        "error_message": "",
        "created_at": "",
        "updated_at": "",
    }

    job = store.confirm_job_by_job_id(job_id="JOB-8")

    assert job is not None
    assert job.status == "queued"


def test_job_store_cancels_waiting_job() -> None:
    fake_db = _FakeDbClient()
    store = JobStore(fake_db)
    fake_db.jobs["JOB-9"] = {
        "id": 9,
        "job_id": "JOB-9",
        "status": "awaiting_confirmation",
        "trigger_source": "dingtalk_bot",
        "trigger_user_id": "",
        "trigger_user_name": "",
        "conversation_type": "",
        "conversation_id": "",
        "repository": "BI",
        "project_key": "sonar-bi",
        "author": "alice@example.com",
        "base_branch": "develop",
        "issue_keys_json": "[]",
        "skip_issue_keys_json": "[]",
        "max_issues": 0,
        "reviewer_email": "",
        "dingtalk_userid": "",
        "target_payload_json": "{}",
        "confirmation_token": "token-9",
        "confirmation_card_instance_id": "",
        "confirmed_at": "",
        "queued_at": "",
        "started_at": "",
        "finished_at": "",
        "run_label": "",
        "result_status": "",
        "pr_url": "",
        "target_summary_path": "",
        "run_log_path": "",
        "error_message": "",
        "created_at": "",
        "updated_at": "",
    }

    job = store.cancel_job(job_id="JOB-9", confirmation_token="token-9")

    assert job is not None
    assert job.status == "cancelled"
    assert job.result_status == "cancelled"
    assert fake_db.updated_jobs[-1][1]["status"] == "cancelled"


def test_job_store_claim_and_finish_job_round_trip() -> None:
    fake_db = _FakeDbClient()
    store = JobStore(fake_db)
    fake_db.claimed_row = {
        "id": 2,
        "job_id": "JOB-2",
        "status": "running",
        "trigger_source": "manual_seed",
        "trigger_user_id": "",
        "trigger_user_name": "",
        "conversation_type": "",
        "conversation_id": "",
        "repository": "BI",
        "project_key": "sonar-bi",
        "author": "alice@example.com",
        "base_branch": "develop",
        "issue_keys_json": json.dumps(["k1", "k2"], ensure_ascii=False),
        "skip_issue_keys_json": json.dumps(["k3"], ensure_ascii=False),
        "max_issues": 2,
        "reviewer_email": "",
        "dingtalk_userid": "",
        "target_payload_json": json.dumps({"solution_path": "Foo.sln"}, ensure_ascii=False),
        "confirmation_token": "",
        "confirmation_card_instance_id": "",
        "confirmed_at": "",
        "queued_at": "",
        "started_at": "",
        "finished_at": "",
        "run_label": "",
        "result_status": "",
        "pr_url": "",
        "target_summary_path": "",
        "run_log_path": "",
        "error_message": "",
        "created_at": "",
        "updated_at": "",
    }
    fake_db.jobs["JOB-2"] = dict(fake_db.claimed_row)

    job = store.claim_next_job()
    assert job is not None
    assert job.issue_keys == ("k1", "k2")
    assert job.skip_issue_keys == ("k3",)

    finished = store.mark_job_finished(
        "JOB-2",
        status="succeeded",
        result_status="succeeded",
        pr_url="https://example/pr/1",
        target_summary_path="logs/run-artifacts/summary.json",
        run_log_path="logs/runs/job_x.log",
        error_message="",
    )
    assert finished is not None
    assert finished.status == "succeeded"
    assert finished.pr_url == "https://example/pr/1"


def test_job_store_records_command_and_recovers_stale_jobs() -> None:
    fake_db = _FakeDbClient()
    store = JobStore(fake_db)

    record = store.record_command(
        job_id="JOB-3",
        raw_text="修复 BI alice@example.com",
        parse_status="parsed",
        parsed_command={"repository": "BI"},
    )
    recovered = store.recover_stale_running_jobs(timeout_seconds=600)

    assert record.id == 9
    assert fake_db.recorded_commands[0]["job_id"] == "JOB-3"
    assert recovered == 3
    assert fake_db.timeout_calls


def test_job_store_counts_active_and_recent_jobs() -> None:
    fake_db = _FakeDbClient()
    fake_db.count_run_jobs_result = 2
    store = JobStore(fake_db)

    active = store.count_active_jobs_for_user("staff-1")
    recent = store.count_recent_jobs_for_user("staff-1", window_seconds=600)

    assert active == 2
    assert recent == 2


def test_job_store_get_latest_job_for_user_and_create_rerun_job() -> None:
    fake_db = _FakeDbClient()
    store = JobStore(fake_db)
    original = store.create_job(
        repository="BI",
        project_key="sonar-bi",
        author="alice@example.com",
        base_branch="develop",
        trigger_user_id="staff-1",
        trigger_user_name="Alice",
        await_confirmation=False,
    )

    latest = store.get_latest_job_for_user("staff-1")
    assert latest is not None
    assert latest.job_id == original.job_id

    rerun = store.create_rerun_job(
        original,
        trigger_user_id="staff-2",
        trigger_user_name="Bob",
        conversation_type="group_chat",
        conversation_id="conv-2",
    )
    assert rerun.status == "awaiting_confirmation"
    assert rerun.trigger_source == "dingtalk_rerun"
    assert rerun.trigger_user_id == "staff-2"
    assert rerun.repository == original.repository
    assert rerun.dingtalk_userid == "staff-2"


def test_job_store_attaches_and_reads_confirmation_card_instance() -> None:
    fake_db = _FakeDbClient()
    store = JobStore(fake_db)
    original = store.create_job(
        repository="BI",
        project_key="sonar-bi",
        author="alice@example.com",
        base_branch="develop",
        trigger_user_id="staff-1",
        trigger_user_name="Alice",
        await_confirmation=True,
    )

    updated = store.attach_confirmation_card_instance(
        job_id=original.job_id,
        confirmation_card_instance_id="card-1",
    )
    by_card = store.get_job_by_confirmation_card_instance_id("card-1")

    assert updated is not None
    assert updated.confirmation_card_instance_id == "card-1"
    assert by_card is not None
    assert by_card.job_id == original.job_id
