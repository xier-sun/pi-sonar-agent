from __future__ import annotations

from types import SimpleNamespace

from dingtalk_worker import DingTalkWorker


def test_dingtalk_worker_run_once_no_job_returns_false() -> None:
    class FakeStore:
        def recover_stale_running_jobs(self, *, timeout_seconds: int) -> int:
            return 0

        def claim_next_job(self):
            return None

    worker = DingTalkWorker(
        job_store=FakeStore(),
        job_runner=SimpleNamespace(),
        poll_interval_seconds=1,
        stale_running_timeout_seconds=600,
    )

    assert worker.run_once() is False


def test_dingtalk_worker_run_once_executes_one_claimed_job() -> None:
    claimed_job = SimpleNamespace(
        job_id="JOB-1",
        repository="BI",
        author="alice@example.com",
        base_branch="develop",
    )
    calls: list[tuple[str, object]] = []

    class FakeStore:
        def recover_stale_running_jobs(self, *, timeout_seconds: int) -> int:
            calls.append(("recover", timeout_seconds))
            return 1

        def claim_next_job(self):
            calls.append(("claim", None))
            return claimed_job

        def mark_job_run_context(self, job_id: str, *, run_label: str, run_log_path: str) -> None:
            calls.append(("started", (job_id, run_label, run_log_path)))

        def mark_job_finished(
            self,
            job_id: str,
            *,
            status: str,
            result_status: str,
            pr_url: str = "",
            target_summary_path: str = "",
            run_log_path: str = "",
            error_message: str = "",
        ):
            calls.append(
                (
                    "finished",
                    {
                        "job_id": job_id,
                        "status": status,
                        "result_status": result_status,
                        "pr_url": pr_url,
                        "target_summary_path": target_summary_path,
                        "run_log_path": run_log_path,
                        "error_message": error_message,
                    },
                )
            )

    class FakeRunner:
        def run_job(self, job, *, on_started=None):
            on_started("20260518193000", "logs/runs/job_20260518193000.log")
            return SimpleNamespace(
                run_label="20260518193000",
                status="partial",
                ok=False,
                pr_url="https://example/pr/3",
                target_summary_path="logs/run-artifacts/run_summary.json",
                run_log_path="logs/runs/job_20260518193000.log",
                error_message="partial completion",
            )

    worker = DingTalkWorker(
        job_store=FakeStore(),
        job_runner=FakeRunner(),
        poll_interval_seconds=1,
        stale_running_timeout_seconds=600,
    )

    assert worker.run_once() is True
    finished = [entry for entry in calls if entry[0] == "finished"][0][1]
    assert finished["status"] == "partial"
    assert finished["result_status"] == "partial"
    assert finished["pr_url"] == "https://example/pr/3"


def test_dingtalk_worker_notifies_start_and_finish_non_blocking() -> None:
    claimed_job = SimpleNamespace(
        job_id="JOB-2",
        repository="BI",
        author="alice@example.com",
        base_branch="develop",
        dingtalk_userid="ding-user",
    )
    calls: list[tuple[str, object]] = []

    class FakeStore:
        def recover_stale_running_jobs(self, *, timeout_seconds: int) -> int:
            return 0

        def claim_next_job(self):
            return claimed_job

        def mark_job_run_context(self, job_id: str, *, run_label: str, run_log_path: str) -> None:
            calls.append(("started", (job_id, run_label, run_log_path)))

        def mark_job_finished(
            self,
            job_id: str,
            *,
            status: str,
            result_status: str,
            pr_url: str = "",
            target_summary_path: str = "",
            run_log_path: str = "",
            error_message: str = "",
        ):
            calls.append(("finished", status))

    class FakeRunner:
        def run_job(self, job, *, on_started=None):
            on_started("20260518200000", "logs/runs/job_20260518200000.log")
            return SimpleNamespace(
                run_label="20260518200000",
                status="succeeded",
                ok=True,
                pr_url="https://example/pr/4",
                target_summary_path="logs/run-artifacts/run_summary.json",
                run_log_path="logs/runs/job_20260518200000.log",
                error_message="",
            )

    class FakeNotifier:
        def __init__(self) -> None:
            self.events: list[tuple[str, str]] = []

        def notify_job_started(self, job, *, run_label: str, run_log_path: str):
            self.events.append(("started", run_label))

        def notify_job_finished(
            self,
            job,
            *,
            terminal_status: str,
            result_status: str,
            run_label: str,
            pr_url: str,
            target_summary_path: str,
            run_log_path: str,
            error_message: str,
        ):
            self.events.append(("finished", terminal_status))

    notifier = FakeNotifier()
    worker = DingTalkWorker(
        job_store=FakeStore(),
        job_runner=FakeRunner(),
        notifier=notifier,
        poll_interval_seconds=1,
        stale_running_timeout_seconds=600,
    )

    assert worker.run_once() is True
    assert notifier.events == [("started", "20260518200000"), ("finished", "succeeded")]
