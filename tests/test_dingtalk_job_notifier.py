from __future__ import annotations

from types import SimpleNamespace

from pi_sonar_agent.core.dingtalk_job_notifier import DingTalkJobNotifier


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []

    def send_markdown_message(
        self,
        title: str,
        text: str,
        userid: str | None = None,
        webhook: str | None = None,
    ):
        self.calls.append((title, text, userid))
        return {"errmsg": "ok"}


def test_notify_job_started_builds_running_message() -> None:
    client = _FakeClient()
    notifier = DingTalkJobNotifier(client=client)
    job = SimpleNamespace(
        job_id="JOB-1",
        repository="BI",
        author="alice@example.com",
        base_branch="develop",
        dingtalk_userid="ding-user",
    )

    notifier.notify_job_started(
        job,
        run_label="20260518195500",
        run_log_path="logs/runs/job_20260518195500.log",
    )

    title, text, userid = client.calls[0]
    assert title.startswith("[RUNNING]")
    assert "任务编号" in text
    assert "run_label" in text
    assert userid == "ding-user"


def test_notify_job_finished_builds_terminal_message() -> None:
    client = _FakeClient()
    notifier = DingTalkJobNotifier(client=client)
    job = SimpleNamespace(
        job_id="JOB-2",
        repository="BI",
        author="alice@example.com",
        base_branch="develop",
        dingtalk_userid="",
    )

    notifier.notify_job_finished(
        job,
        terminal_status="partial",
        result_status="partial",
        run_label="20260518195600",
        pr_url="https://example/pr/1",
        target_summary_path="logs/run-artifacts/run_summary.json",
        run_log_path="logs/runs/job_20260518195600.log",
        error_message="partial completion",
    )

    title, text, userid = client.calls[0]
    assert title.startswith("[WARN]")
    assert "PR 链接" in text
    assert "运行摘要" in text
    assert "partial completion" in text
    assert userid is None
