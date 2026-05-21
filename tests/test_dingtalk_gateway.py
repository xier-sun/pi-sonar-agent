from __future__ import annotations

import json
from pathlib import Path

from pi_sonar_agent.core.dingtalk_access_policy import DingTalkAccessPolicy
from pi_sonar_agent.core.job_store import RunJob
from pi_sonar_agent.dingtalk_gateway import (
    DingTalkGateway,
    load_targets_registry,
    resolve_fix_request_against_targets,
)
from pi_sonar_agent.integrations.dingtalk_bot import DingTalkFixCommand


class _FakeJobStore:
    def __init__(self) -> None:
        self.commands: list[dict] = []
        self.jobs: list[dict] = []
        self.existing_command = None
        self.job_rows: dict[str, RunJob] = {}

    def get_command_record_by_message_id(self, message_id: str):
        return self.existing_command

    def record_command(self, **kwargs):
        self.commands.append(dict(kwargs))
        return None

    def get_job(self, job_id: str):
        return self.job_rows.get(job_id)

    def get_job_by_confirmation_card_instance_id(self, card_instance_id: str):
        for job in self.job_rows.values():
            if job.confirmation_card_instance_id == card_instance_id:
                return job
        return None

    def get_latest_job_for_user(self, trigger_user_id: str):
        jobs = [
            job
            for job in self.job_rows.values()
            if job.trigger_user_id == trigger_user_id
        ]
        if not jobs:
            return None
        jobs.sort(key=lambda item: item.job_id, reverse=True)
        return jobs[0]

    def get_latest_awaiting_confirmation_job_for_user(self, trigger_user_id: str, *, conversation_id: str = ""):
        jobs = [
            job
            for job in self.job_rows.values()
            if job.trigger_user_id == trigger_user_id
            and job.status == "awaiting_confirmation"
            and (
                not conversation_id
                or job.conversation_id == conversation_id
            )
        ]
        if not jobs:
            return None
        jobs.sort(key=lambda item: item.job_id, reverse=True)
        return jobs[0]

    def get_latest_active_job_for_user(self, trigger_user_id: str, *, conversation_id: str = ""):
        jobs = [
            job
            for job in self.job_rows.values()
            if job.trigger_user_id == trigger_user_id
            and job.status in {"awaiting_confirmation", "queued", "running"}
            and (
                not conversation_id
                or job.conversation_id == conversation_id
            )
        ]
        if not jobs:
            return None
        jobs.sort(key=lambda item: item.job_id, reverse=True)
        return jobs[0]

    def count_active_jobs_for_user(self, trigger_user_id: str) -> int:
        return sum(
            1
            for job in self.job_rows.values()
            if job.trigger_user_id == trigger_user_id
            and job.status in {"awaiting_confirmation", "queued", "running"}
        )

    def count_recent_jobs_for_user(self, trigger_user_id: str, *, window_seconds: int) -> int:
        return sum(1 for job in self.job_rows.values() if job.trigger_user_id == trigger_user_id)

    def create_job(self, **kwargs):
        self.jobs.append(dict(kwargs))
        job_id = f"JOB-{len(self.job_rows) + 1}"
        job = RunJob(
            id=1,
            job_id=job_id,
            status="awaiting_confirmation",
            trigger_source="dingtalk_bot",
            trigger_user_id=kwargs.get("trigger_user_id", ""),
            trigger_user_name=kwargs.get("trigger_user_name", ""),
            conversation_type=kwargs.get("conversation_type", ""),
            conversation_id=kwargs.get("conversation_id", ""),
            repository=kwargs["repository"],
            project_key=kwargs["project_key"],
            author=kwargs["author"],
            base_branch=kwargs["base_branch"],
            issue_keys=tuple(kwargs.get("issue_keys", ()) or ()),
            skip_issue_keys=tuple(kwargs.get("skip_issue_keys", ()) or ()),
            max_issues=int(kwargs.get("max_issues", 0) or 0),
            reviewer_email=kwargs.get("reviewer_email", ""),
            dingtalk_userid=kwargs.get("dingtalk_userid", ""),
            target_payload=dict(kwargs.get("target_payload", {}) or {}),
            confirmation_token="token-1",
            confirmation_card_instance_id="",
            confirmed_at="",
            queued_at="",
            started_at="",
            finished_at="",
            run_label="",
            result_status="",
            pr_url="",
            target_summary_path="",
            run_log_path="",
            error_message="",
            created_at="",
            updated_at="",
        )
        self.job_rows[job.job_id] = job
        return job

    def attach_confirmation_card_instance(self, *, job_id: str, confirmation_card_instance_id: str):
        job = self.job_rows.get(job_id)
        if job is None:
            return None
        updated = RunJob(
            **{
                **job.__dict__,
                "confirmation_card_instance_id": confirmation_card_instance_id,
            }
        )
        self.job_rows[job_id] = updated
        return updated

    def update_awaiting_confirmation_job(self, job_id: str, **kwargs):
        job = self.job_rows.get(job_id)
        if job is None or job.status != "awaiting_confirmation":
            return None
        updated = RunJob(
            **{
                **job.__dict__,
                "repository": kwargs["repository"],
                "project_key": kwargs["project_key"],
                "author": kwargs["author"],
                "base_branch": kwargs["base_branch"],
                "issue_keys": tuple(kwargs.get("issue_keys", ()) or ()),
                "skip_issue_keys": tuple(kwargs.get("skip_issue_keys", ()) or ()),
                "max_issues": int(kwargs.get("max_issues", 0) or 0),
                "reviewer_email": kwargs.get("reviewer_email", ""),
                "dingtalk_userid": kwargs.get("dingtalk_userid", ""),
                "target_payload": dict(kwargs.get("target_payload", {}) or {}),
                "confirmation_card_instance_id": "",
            }
        )
        self.job_rows[job_id] = updated
        return updated

    def create_rerun_job(
        self,
        original_job: RunJob,
        *,
        trigger_user_id: str,
        trigger_user_name: str,
        conversation_type: str,
        conversation_id: str,
    ):
        return self.create_job(
            repository=original_job.repository,
            project_key=original_job.project_key,
            author=original_job.author,
            base_branch=original_job.base_branch,
            issue_keys=original_job.issue_keys,
            skip_issue_keys=original_job.skip_issue_keys,
            max_issues=original_job.max_issues,
            reviewer_email=original_job.reviewer_email,
            dingtalk_userid=original_job.dingtalk_userid,
            target_payload=original_job.target_payload,
            trigger_user_id=trigger_user_id,
            trigger_user_name=trigger_user_name,
            conversation_type=conversation_type,
            conversation_id=conversation_id,
            await_confirmation=True,
        )

    def confirm_job(self, *, job_id: str, confirmation_token: str):
        job = self.job_rows.get(job_id)
        if job is None or job.confirmation_token != confirmation_token:
            return None
        if job.status == "queued":
            return job
        if job.status != "awaiting_confirmation":
            return None
        confirmed = RunJob(
            **{
                **job.__dict__,
                "status": "queued",
                "queued_at": "2026-05-18 10:00:00",
                "confirmed_at": "2026-05-18 10:00:00",
            }
        )
        self.job_rows[job_id] = confirmed
        return confirmed

    def confirm_job_by_job_id(self, *, job_id: str):
        job = self.job_rows.get(job_id)
        if job is None:
            return None
        if job.status == "queued":
            return job
        if job.status != "awaiting_confirmation":
            return None
        confirmed = RunJob(
            **{
                **job.__dict__,
                "status": "queued",
                "queued_at": "2026-05-18 10:00:00",
                "confirmed_at": "2026-05-18 10:00:00",
            }
        )
        self.job_rows[job_id] = confirmed
        return confirmed

    def cancel_job(self, *, job_id: str, confirmation_token: str):
        job = self.job_rows.get(job_id)
        if job is None or job.confirmation_token != confirmation_token:
            return None
        if job.status == "cancelled":
            return job
        if job.status not in {"awaiting_confirmation", "queued"}:
            return None
        cancelled = RunJob(
            **{
                **job.__dict__,
                "status": "cancelled",
                "result_status": "cancelled",
                "finished_at": "2026-05-18 10:01:00",
                "error_message": "cancelled by user before execution",
            }
        )
        self.job_rows[job_id] = cancelled
        return cancelled

    def cancel_job_by_job_id(self, *, job_id: str):
        job = self.job_rows.get(job_id)
        if job is None:
            return None
        if job.status == "cancelled":
            return job
        if job.status not in {"awaiting_confirmation", "queued"}:
            return None
        cancelled = RunJob(
            **{
                **job.__dict__,
                "status": "cancelled",
                "result_status": "cancelled",
                "finished_at": "2026-05-18 10:02:00",
                "error_message": "cancelled by user before execution",
            }
        )
        self.job_rows[job_id] = cancelled
        return cancelled


def test_resolve_fix_request_against_targets_merges_notification_only_duplicates() -> None:
    command = DingTalkFixCommand(repository="BI", author="alice@example.com")
    targets = [
        {
            "project_key": "sonar-bi",
            "repository": "BI",
            "author": "alice@example.com",
            "solution_path": "Foo.sln",
            "max_issues": 50,
            "dingtalk_userid": "ding-1",
        },
        {
            "project_key": "sonar-bi",
            "repository": "BI",
            "author": "alice@example.com",
            "solution_path": "Foo.sln",
            "max_issues": 50,
            "dingtalk_userid": "ding-2",
        },
    ]

    resolved = resolve_fix_request_against_targets(command, targets)

    assert resolved["project_key"] == "sonar-bi"
    assert resolved["repository"] == "BI"
    assert resolved["author"] == "alice@example.com"
    assert resolved["dingtalk_userid"] == "ding-1"
    assert resolved["base_branch"] == "develop"


def test_resolve_fix_request_against_targets_prioritizes_command_dingtalk_userid() -> None:
    command = DingTalkFixCommand(
        repository="BI",
        author="alice@example.com",
        dingtalk_userid="ding-command",
    )
    targets = [
        {
            "project_key": "sonar-bi",
            "repository": "BI",
            "author": "alice@example.com",
            "base_branch": "develop",
            "max_issues": 50,
            "dingtalk_userid": "ding-target",
        }
    ]

    resolved = resolve_fix_request_against_targets(command, targets)

    assert resolved["dingtalk_userid"] == "ding-command"


def test_load_targets_registry_requires_array(tmp_path: Path) -> None:
    bad_file = tmp_path / "targets.json"
    bad_file.write_text("{}", encoding="utf-8")

    try:
        load_targets_registry(bad_file)
    except ValueError as exc:
        assert "根节点必须是数组" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_gateway_creates_awaiting_confirmation_job_and_records_command(tmp_path: Path) -> None:
    targets_file = tmp_path / "targets.json"
    targets_file.write_text(
        json.dumps(
            [
                {
                    "project_key": "sonar-bi",
                    "repository": "BI",
                    "author": "alice@example.com",
                    "base_branch": "develop",
                    "solution_path": "Foo.sln",
                    "max_issues": 50,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    store = _FakeJobStore()
    gateway = DingTalkGateway(job_store=store, targets_path=targets_file)

    result = gateway.handle_event_payload(
        {
            "msgId": "MSG-1",
            "senderStaffId": "staff-1",
            "senderNick": "Alice",
            "conversationType": "group_chat",
            "conversationId": "conv-1",
            "text": {
                "content": "修复 BI alice@example.com issue_keys=i1,i2 skip_issue_keys=i3 max_issues=5"
            },
        }
    )

    assert result.status == "awaiting_confirmation"
    assert result.job_id == "JOB-1"
    assert "任务编号: JOB-1" in result.reply_text
    assert result.reply_card is not None
    assert result.reply_card["title"] == "确认执行 Sonar 自动修复"
    assert store.jobs[0]["trigger_source"] == "dingtalk_bot"
    assert store.jobs[0]["issue_keys"] == ("i1", "i2")
    assert store.jobs[0]["skip_issue_keys"] == ("i3",)
    assert store.jobs[0]["dingtalk_userid"] == "staff-1"
    assert store.commands[0]["job_id"] == "JOB-1"
    assert store.commands[0]["parse_status"] == "parsed"
    assert "审阅者账号:" in result.reply_text


def test_gateway_updates_existing_waiting_job_with_follow_up_options(tmp_path: Path) -> None:
    targets_file = tmp_path / "targets.json"
    targets_file.write_text(
        json.dumps(
            [
                {
                    "project_key": "sonar-bi",
                    "repository": "BI",
                    "author": "alice@example.com",
                    "base_branch": "develop",
                    "solution_path": "Foo.sln",
                    "max_issues": 50,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    store = _FakeJobStore()
    gateway = DingTalkGateway(job_store=store, targets_path=targets_file)
    created = store.create_job(
        repository="BI",
        project_key="sonar-bi",
        author="alice@example.com",
        base_branch="develop",
        max_issues=50,
        trigger_user_id="staff-1",
        trigger_user_name="Alice",
        conversation_type="group_chat",
        conversation_id="conv-1",
        await_confirmation=True,
    )

    result = gateway.handle_event_payload(
        {
            "msgId": "MSG-update-1",
            "senderStaffId": "staff-1",
            "senderNick": "Alice",
            "conversationType": "group_chat",
            "conversationId": "conv-1",
            "text": {
                "content": "skip_issue_keys=i3 reviewer_email=rv@example.com max_issues=3"
            },
        }
    )

    assert result.status == "awaiting_confirmation"
    assert result.job_id == created.job_id
    updated = store.get_job(created.job_id)
    assert updated is not None
    assert updated.skip_issue_keys == ("i3",)
    assert updated.reviewer_email == "rv@example.com"
    assert updated.max_issues == 3
    assert "已更新待确认任务" in result.reply_text
    assert "本次补充/修改:" in result.reply_text


def test_gateway_explicit_new_author_rebases_waiting_job_from_target_defaults(tmp_path: Path) -> None:
    targets_file = tmp_path / "targets.json"
    targets_file.write_text(
        json.dumps(
            [
                {
                    "project_key": "sonar-bi",
                    "repository": "BI",
                    "author": "alice@example.com",
                    "base_branch": "develop",
                    "solution_path": "Foo.sln",
                    "max_issues": 50,
                    "issue_keys": ["i1"],
                    "skip_issue_keys": ["s1"],
                    "reviewer_email": "alice-reviewer@example.com",
                },
                {
                    "project_key": "sonar-bi",
                    "repository": "BI",
                    "author": "bob@example.com",
                    "base_branch": "release",
                    "solution_path": "Foo.sln",
                    "max_issues": 120,
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    store = _FakeJobStore()
    gateway = DingTalkGateway(job_store=store, targets_path=targets_file)
    created = store.create_job(
        repository="BI",
        project_key="sonar-bi",
        author="alice@example.com",
        base_branch="develop",
        issue_keys=("i1",),
        skip_issue_keys=("s1",),
        max_issues=50,
        reviewer_email="alice-reviewer@example.com",
        trigger_user_id="staff-1",
        trigger_user_name="Alice",
        conversation_type="group_chat",
        conversation_id="conv-1",
        await_confirmation=True,
    )

    result = gateway.handle_event_payload(
        {
            "msgId": "MSG-update-2",
            "senderStaffId": "staff-1",
            "senderNick": "Alice",
            "conversationType": "group_chat",
            "conversationId": "conv-1",
            "text": {"content": "修复 BI bob@example.com"},
        }
    )

    assert result.status == "awaiting_confirmation"
    assert result.job_id == created.job_id
    updated = store.get_job(created.job_id)
    assert updated is not None
    assert updated.author == "bob@example.com"
    assert updated.base_branch == "release"
    assert updated.issue_keys == ()
    assert updated.skip_issue_keys == ()
    assert updated.max_issues == 120
    assert updated.reviewer_email == "bob@example.com"
    assert "作者: bob@example.com" in result.reply_text
    assert "skip_issue_keys: (未指定)" in result.reply_text
    assert "max_issues: 120" in result.reply_text
    assert "审阅者账号: bob@example.com" in result.reply_text


def test_gateway_requests_missing_required_fields_before_creating_job(tmp_path: Path) -> None:
    targets_file = tmp_path / "targets.json"
    targets_file.write_text("[]", encoding="utf-8")
    store = _FakeJobStore()
    gateway = DingTalkGateway(job_store=store, targets_path=targets_file)

    result = gateway.handle_event_payload(
        {
            "msgId": "MSG-partial-1",
            "senderStaffId": "staff-1",
            "senderNick": "Alice",
            "conversationType": "group_chat",
            "conversationId": "conv-1",
            "text": {"content": "修复 BI"},
        }
    )

    assert result.status == "need_more_context"
    assert "还缺: author" in result.reply_text
    assert not store.jobs


def test_gateway_partial_fix_command_does_not_inherit_author_from_existing_draft(tmp_path: Path) -> None:
    targets_file = tmp_path / "targets.json"
    targets_file.write_text("[]", encoding="utf-8")
    store = _FakeJobStore()
    store.create_job(
        repository="BI",
        project_key="sonar-bi",
        author="alice@example.com",
        base_branch="develop",
        trigger_user_id="staff-1",
        trigger_user_name="Alice",
        conversation_type="group_chat",
        conversation_id="conv-1",
        await_confirmation=True,
    )
    gateway = DingTalkGateway(job_store=store, targets_path=targets_file)

    result = gateway.handle_event_payload(
        {
            "msgId": "MSG-partial-2",
            "senderStaffId": "staff-1",
            "senderNick": "Alice",
            "conversationType": "group_chat",
            "conversationId": "conv-1",
            "text": {"content": "修复 BI"},
        }
    )

    assert result.status == "need_more_context"
    assert "还缺: author" in result.reply_text
    assert "当前作者:" not in result.reply_text


def test_gateway_rejects_duplicate_message_without_creating_new_job(tmp_path: Path) -> None:
    targets_file = tmp_path / "targets.json"
    targets_file.write_text("[]", encoding="utf-8")
    store = _FakeJobStore()
    store.existing_command = type(
        "ExistingCommand",
        (),
        {"job_id": "JOB-42"},
    )()
    gateway = DingTalkGateway(job_store=store, targets_path=targets_file)

    result = gateway.handle_event_payload(
        {
            "msgId": "MSG-dup",
            "text": {"content": "修复 BI alice@example.com"},
        }
    )

    assert result.status == "duplicate"
    assert result.job_id == "JOB-42"
    assert not store.jobs
    assert not store.commands


def test_gateway_confirms_job_via_callback_payload(tmp_path: Path) -> None:
    targets_file = tmp_path / "targets.json"
    targets_file.write_text("[]", encoding="utf-8")
    store = _FakeJobStore()
    gateway = DingTalkGateway(job_store=store, targets_path=targets_file)
    store.create_job(
        repository="BI",
        project_key="sonar-bi",
        author="alice@example.com",
        base_branch="develop",
        await_confirmation=True,
    )

    result = gateway.handle_confirmation_payload(
        {
            "cardPrivateData": {
                "action": "confirm_fix_job",
                "job_id": "JOB-1",
                "confirmation_token": "token-1",
            }
        }
    )

    assert result.status == "confirmed"
    assert result.job_id == "JOB-1"
    assert "已确认执行" in result.reply_text
    assert store.get_job("JOB-1").status == "queued"


def test_gateway_confirms_job_via_card_instance_callback_payload(tmp_path: Path) -> None:
    targets_file = tmp_path / "targets.json"
    targets_file.write_text("[]", encoding="utf-8")
    store = _FakeJobStore()
    gateway = DingTalkGateway(
        job_store=store,
        targets_path=targets_file,
        access_policy=DingTalkAccessPolicy(allowed_staff_ids=("staff-1",)),
    )
    store.create_job(
        repository="BI",
        project_key="sonar-bi",
        author="alice@example.com",
        base_branch="develop",
        trigger_user_id="staff-1",
        await_confirmation=True,
    )
    store.attach_confirmation_card_instance(
        job_id="JOB-1",
        confirmation_card_instance_id="card-1",
    )

    result = gateway.handle_confirmation_payload(
        {
            "outTrackId": "card-1",
            "userId": "staff-1",
            "content": {
                "cardPrivateData": {
                    "actionIds": ["confirm_fix_job"],
                }
            },
        }
    )

    assert result.status == "confirmed"
    assert result.job_id == "JOB-1"
    assert store.get_job("JOB-1").status == "queued"


def test_gateway_confirms_job_via_stream_template_accept_action(tmp_path: Path) -> None:
    targets_file = tmp_path / "targets.json"
    targets_file.write_text("[]", encoding="utf-8")
    store = _FakeJobStore()
    gateway = DingTalkGateway(
        job_store=store,
        targets_path=targets_file,
        access_policy=DingTalkAccessPolicy(allowed_staff_ids=("staff-1",)),
    )
    store.create_job(
        repository="BI",
        project_key="sonar-bi",
        author="alice@example.com",
        base_branch="develop",
        trigger_user_id="staff-1",
        await_confirmation=True,
    )
    store.attach_confirmation_card_instance(
        job_id="JOB-1",
        confirmation_card_instance_id="card-accept-1",
    )

    result = gateway.handle_confirmation_payload(
        {
            "outTrackId": "card-accept-1",
            "userId": "staff-1",
            "content": {
                "action": "accept",
            },
        }
    )

    assert result.status == "confirmed"
    assert result.job_id == "JOB-1"
    assert store.get_job("JOB-1").status == "queued"


def test_gateway_handles_repeat_confirm_and_cancel_idempotently(tmp_path: Path) -> None:
    targets_file = tmp_path / "targets.json"
    targets_file.write_text("[]", encoding="utf-8")
    store = _FakeJobStore()
    gateway = DingTalkGateway(job_store=store, targets_path=targets_file)
    store.create_job(
        repository="BI",
        project_key="sonar-bi",
        author="alice@example.com",
        base_branch="develop",
        await_confirmation=True,
    )

    first = gateway.handle_confirmation_payload(
        {
            "cardPrivateData": {
                "action": "confirm_fix_job",
                "job_id": "JOB-1",
                "confirmation_token": "token-1",
            }
        }
    )
    second = gateway.handle_confirmation_payload(
        {
            "cardPrivateData": {
                "action": "confirm_fix_job",
                "job_id": "JOB-1",
                "confirmation_token": "token-1",
            }
        }
    )
    cancelled = gateway.handle_confirmation_payload(
        {
            "cardPrivateData": {
                "action": "cancel_fix_job",
                "job_id": "JOB-1",
                "confirmation_token": "token-1",
            }
        }
    )

    assert first.status == "confirmed"
    assert second.status == "already_confirmed"
    assert cancelled.status == "cancelled"


def test_gateway_rejects_invalid_confirmation_payload(tmp_path: Path) -> None:
    targets_file = tmp_path / "targets.json"
    targets_file.write_text("[]", encoding="utf-8")
    store = _FakeJobStore()
    gateway = DingTalkGateway(job_store=store, targets_path=targets_file)

    result = gateway.handle_confirmation_payload(
        {
            "cardPrivateData": {
                "action": "confirm_fix_job",
                "job_id": "JOB-404",
                "confirmation_token": "bad-token",
            }
        }
    )

    assert result.status == "invalid_confirmation"
    assert "确认信息无效" in result.reply_text


def test_gateway_blocks_non_whitelisted_sender(tmp_path: Path) -> None:
    targets_file = tmp_path / "targets.json"
    targets_file.write_text(
        json.dumps(
            [
                {
                    "project_key": "sonar-bi",
                    "repository": "BI",
                    "author": "alice@example.com",
                    "base_branch": "develop",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    store = _FakeJobStore()
    gateway = DingTalkGateway(
        job_store=store,
        targets_path=targets_file,
        access_policy=DingTalkAccessPolicy(allowed_staff_ids=("staff-allow",)),
    )

    result = gateway.handle_event_payload(
        {
            "msgId": "MSG-unauth",
            "senderStaffId": "staff-deny",
            "senderNick": "Bob",
            "conversationType": "group_chat",
            "conversationId": "conv-1",
            "text": {"content": "修复 BI alice@example.com"},
        }
    )

    assert result.status == "unauthorized"
    assert not store.jobs


def test_gateway_blocks_rate_limited_sender(tmp_path: Path) -> None:
    targets_file = tmp_path / "targets.json"
    targets_file.write_text(
        json.dumps(
            [
                {
                    "project_key": "sonar-bi",
                    "repository": "BI",
                    "author": "alice@example.com",
                    "base_branch": "develop",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    store = _FakeJobStore()
    store.create_job(
        repository="BI",
        project_key="sonar-bi",
        author="alice@example.com",
        base_branch="develop",
        trigger_user_id="staff-1",
        await_confirmation=True,
    )
    gateway = DingTalkGateway(
        job_store=store,
        targets_path=targets_file,
        access_policy=DingTalkAccessPolicy(max_active_jobs_per_user=1),
    )

    result = gateway.handle_event_payload(
        {
            "msgId": "MSG-rate",
            "senderStaffId": "staff-1",
            "senderNick": "Alice",
            "conversationType": "group_chat",
            "conversationId": "conv-1",
            "text": {"content": "修复 BI alice@example.com"},
        }
    )

    assert result.status == "rate_limited"
    assert len(store.jobs) == 1


def test_gateway_cancel_command_requires_creator_or_admin(tmp_path: Path) -> None:
    targets_file = tmp_path / "targets.json"
    targets_file.write_text("[]", encoding="utf-8")
    store = _FakeJobStore()
    store.create_job(
        repository="BI",
        project_key="sonar-bi",
        author="alice@example.com",
        base_branch="develop",
        trigger_user_id="staff-1",
        await_confirmation=True,
    )

    denied_gateway = DingTalkGateway(
        job_store=store,
        targets_path=targets_file,
        access_policy=DingTalkAccessPolicy(allowed_staff_ids=("staff-1", "staff-2")),
    )
    denied = denied_gateway.handle_event_payload(
        {
            "msgId": "MSG-cancel-1",
            "senderStaffId": "staff-2",
            "senderNick": "Bob",
            "conversationType": "group_chat",
            "conversationId": "conv-1",
            "text": {"content": "取消任务 JOB-1"},
        }
    )
    assert denied.status == "unauthorized"

    admin_gateway = DingTalkGateway(
        job_store=store,
        targets_path=targets_file,
        access_policy=DingTalkAccessPolicy(
            allowed_staff_ids=("staff-1", "staff-2"),
            admin_staff_ids=("staff-2",),
        ),
    )
    allowed = admin_gateway.handle_event_payload(
        {
            "msgId": "MSG-cancel-2",
            "senderStaffId": "staff-2",
            "senderNick": "Bob",
            "conversationType": "group_chat",
            "conversationId": "conv-1",
            "text": {"content": "取消任务 JOB-1"},
        }
    )
    assert allowed.status == "cancelled"
    assert store.get_job("JOB-1").status == "cancelled"


def test_gateway_cancel_current_job_alias_cancels_latest_active_draft(tmp_path: Path) -> None:
    targets_file = tmp_path / "targets.json"
    targets_file.write_text("[]", encoding="utf-8")
    store = _FakeJobStore()
    store.create_job(
        repository="BI",
        project_key="sonar-bi",
        author="alice@example.com",
        base_branch="develop",
        trigger_user_id="staff-1",
        conversation_type="group_chat",
        conversation_id="conv-1",
        await_confirmation=True,
    )
    gateway = DingTalkGateway(
        job_store=store,
        targets_path=targets_file,
        access_policy=DingTalkAccessPolicy(allowed_staff_ids=("staff-1",)),
    )

    result = gateway.handle_event_payload(
        {
            "msgId": "MSG-cancel-current-1",
            "senderStaffId": "staff-1",
            "senderNick": "Alice",
            "conversationType": "group_chat",
            "conversationId": "conv-1",
            "text": {"content": "停止修复"},
        }
    )

    assert result.status == "cancelled"
    assert result.job_id == "JOB-1"
    assert store.get_job("JOB-1").status == "cancelled"


def test_gateway_cancel_current_job_alias_reports_running_job_not_interruptible(tmp_path: Path) -> None:
    targets_file = tmp_path / "targets.json"
    targets_file.write_text("[]", encoding="utf-8")
    store = _FakeJobStore()
    store.create_job(
        repository="BI",
        project_key="sonar-bi",
        author="alice@example.com",
        base_branch="develop",
        trigger_user_id="staff-1",
        conversation_type="group_chat",
        conversation_id="conv-1",
        await_confirmation=False,
    )
    store.job_rows["JOB-1"] = RunJob(
        **{
            **store.job_rows["JOB-1"].__dict__,
            "status": "running",
        }
    )
    gateway = DingTalkGateway(
        job_store=store,
        targets_path=targets_file,
        access_policy=DingTalkAccessPolicy(allowed_staff_ids=("staff-1",)),
    )

    result = gateway.handle_event_payload(
        {
            "msgId": "MSG-cancel-current-2",
            "senderStaffId": "staff-1",
            "senderNick": "Alice",
            "conversationType": "group_chat",
            "conversationId": "conv-1",
            "text": {"content": "取消"},
        }
    )

    assert result.status == "cannot_cancel"
    assert "暂不支持强制中断" in result.reply_text
    assert store.get_job("JOB-1").status == "running"


def test_gateway_confirm_command_requires_creator_or_admin(tmp_path: Path) -> None:
    targets_file = tmp_path / "targets.json"
    targets_file.write_text("[]", encoding="utf-8")
    store = _FakeJobStore()
    store.create_job(
        repository="BI",
        project_key="sonar-bi",
        author="alice@example.com",
        base_branch="develop",
        trigger_user_id="staff-1",
        await_confirmation=True,
    )

    denied_gateway = DingTalkGateway(
        job_store=store,
        targets_path=targets_file,
        access_policy=DingTalkAccessPolicy(allowed_staff_ids=("staff-1", "staff-2")),
    )
    denied = denied_gateway.handle_event_payload(
        {
            "msgId": "MSG-confirm-1",
            "senderStaffId": "staff-2",
            "senderNick": "Bob",
            "conversationType": "group_chat",
            "conversationId": "conv-1",
            "text": {"content": "确认任务 JOB-1"},
        }
    )
    assert denied.status == "unauthorized"

    creator_gateway = DingTalkGateway(
        job_store=store,
        targets_path=targets_file,
        access_policy=DingTalkAccessPolicy(allowed_staff_ids=("staff-1", "staff-2")),
    )
    allowed = creator_gateway.handle_event_payload(
        {
            "msgId": "MSG-confirm-2",
            "senderStaffId": "staff-1",
            "senderNick": "Alice",
            "conversationType": "group_chat",
            "conversationId": "conv-1",
            "text": {"content": "确认任务 JOB-1"},
        }
    )
    assert allowed.status == "confirmed"
    assert store.get_job("JOB-1").status == "queued"


def test_gateway_show_job_command_returns_status_summary(tmp_path: Path) -> None:
    targets_file = tmp_path / "targets.json"
    targets_file.write_text("[]", encoding="utf-8")
    store = _FakeJobStore()
    store.create_job(
        repository="BI",
        project_key="sonar-bi",
        author="alice@example.com",
        base_branch="develop",
        trigger_user_id="staff-1",
        await_confirmation=True,
    )
    gateway = DingTalkGateway(
        job_store=store,
        targets_path=targets_file,
        access_policy=DingTalkAccessPolicy(allowed_staff_ids=("staff-1",)),
    )

    result = gateway.handle_event_payload(
        {
            "msgId": "MSG-show-1",
            "senderStaffId": "staff-1",
            "senderNick": "Alice",
            "conversationType": "group_chat",
            "conversationId": "conv-1",
            "text": {"content": "查看任务 JOB-1"},
        }
    )

    assert result.status == "job_status"
    assert "当前状态: awaiting_confirmation" in result.reply_text
    assert "确认任务 JOB-1" in result.reply_text


def test_gateway_show_recent_job_command_returns_latest_for_sender(tmp_path: Path) -> None:
    targets_file = tmp_path / "targets.json"
    targets_file.write_text("[]", encoding="utf-8")
    store = _FakeJobStore()
    store.create_job(
        repository="BI",
        project_key="sonar-bi",
        author="alice@example.com",
        base_branch="develop",
        trigger_user_id="staff-1",
        trigger_user_name="Alice",
        await_confirmation=False,
    )
    gateway = DingTalkGateway(
        job_store=store,
        targets_path=targets_file,
        access_policy=DingTalkAccessPolicy(allowed_staff_ids=("staff-1",)),
    )

    result = gateway.handle_event_payload(
        {
            "msgId": "MSG-show-recent",
            "senderStaffId": "staff-1",
            "senderNick": "Alice",
            "conversationType": "group_chat",
            "conversationId": "conv-1",
            "text": {"content": "查看我最近一次修复"},
        }
    )

    assert result.status == "job_status"
    assert "Alice，这是你最近一次修复任务" in result.reply_text


def test_gateway_rerun_job_command_creates_new_waiting_job(tmp_path: Path) -> None:
    targets_file = tmp_path / "targets.json"
    targets_file.write_text("[]", encoding="utf-8")
    store = _FakeJobStore()
    original = store.create_job(
        repository="BI",
        project_key="sonar-bi",
        author="alice@example.com",
        base_branch="develop",
        trigger_user_id="staff-1",
        trigger_user_name="Alice",
        await_confirmation=False,
    )
    finished = RunJob(
        **{
            **original.__dict__,
            "status": "succeeded",
            "result_status": "succeeded",
            "run_label": "run-1",
        }
    )
    store.job_rows[original.job_id] = finished
    gateway = DingTalkGateway(
        job_store=store,
        targets_path=targets_file,
        access_policy=DingTalkAccessPolicy(allowed_staff_ids=("staff-1",)),
    )

    result = gateway.handle_event_payload(
        {
            "msgId": "MSG-rerun-1",
            "senderStaffId": "staff-1",
            "senderNick": "Alice",
            "conversationType": "group_chat",
            "conversationId": "conv-1",
            "text": {"content": f"重跑任务 {original.job_id}"},
        }
    )

    assert result.status == "awaiting_confirmation"
    assert result.job_id == "JOB-2"
    assert "创建重跑请求" in result.reply_text
    assert result.reply_card is not None
