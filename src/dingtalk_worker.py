"""Background worker for manually triggered DingTalk jobs."""

from __future__ import annotations

import argparse
import time

from pi_sonar_agent.core.dingtalk_job_notifier import (
    DingTalkJobNotifier,
    create_dingtalk_job_notifier_from_env,
)
from pi_sonar_agent.core.job_runner import JobRunner
from pi_sonar_agent.core.job_store import JobStore, RunJob, create_job_store_from_env


class DingTalkWorker:
    """Serial worker that executes queued manual jobs one by one."""

    def __init__(
        self,
        *,
        job_store: JobStore,
        job_runner: JobRunner,
        notifier: DingTalkJobNotifier | None = None,
        poll_interval_seconds: int = 5,
        stale_running_timeout_seconds: int = 7200,
    ) -> None:
        self.job_store = job_store
        self.job_runner = job_runner
        self.notifier = notifier
        self.poll_interval_seconds = max(int(poll_interval_seconds), 1)
        self.stale_running_timeout_seconds = max(int(stale_running_timeout_seconds), 60)

    def run_once(self) -> bool:
        """Run at most one queued job. Return True when a job was consumed."""

        recovered = self.job_store.recover_stale_running_jobs(
            timeout_seconds=self.stale_running_timeout_seconds
        )
        if recovered:
            print(f"[WARN] 已将 {recovered} 个卡住的 running 任务标记为 timeout")

        job = self.job_store.claim_next_job()
        if job is None:
            print("[INFO] 当前没有 queued 任务")
            return False

        print(
            f"[INFO] 开始执行任务 {job.job_id}: "
            f"{job.repository} | {job.author} | {job.base_branch}"
        )
        result = self.job_runner.run_job(
            job,
            on_started=lambda run_label, run_log_path: self._handle_job_started(
                job,
                run_label=run_label,
                run_log_path=run_log_path,
            ),
        )
        terminal_status = (
            result.status
            if result.status in {"succeeded", "partial", "failed", "cancelled", "timeout"}
            else "failed"
        )
        self.job_store.mark_job_finished(
            job.job_id,
            status=terminal_status,
            result_status=result.status,
            pr_url=result.pr_url,
            target_summary_path=result.target_summary_path,
            run_log_path=result.run_log_path,
            error_message=result.error_message,
        )
        self._notify_finished(
            job,
            terminal_status=terminal_status,
            result_status=result.status,
            run_label=result.run_label,
            pr_url=result.pr_url,
            target_summary_path=result.target_summary_path,
            run_log_path=result.run_log_path,
            error_message=result.error_message,
        )
        print(
            f"[INFO] 任务 {job.job_id} 执行完成: status={terminal_status}, "
            f"run_label={result.run_label}"
        )
        return True

    def _handle_job_started(self, job: RunJob, *, run_label: str, run_log_path: str) -> None:
        self.job_store.mark_job_run_context(
            job.job_id,
            run_label=run_label,
            run_log_path=run_log_path,
        )
        if self.notifier is None:
            return
        try:
            self.notifier.notify_job_started(
                job,
                run_label=run_label,
                run_log_path=run_log_path,
            )
        except Exception as exc:
            print(f"[WARN] 发送钉钉开始通知失败: {exc}")

    def _notify_finished(
        self,
        job: RunJob,
        *,
        terminal_status: str,
        result_status: str,
        run_label: str,
        pr_url: str,
        target_summary_path: str,
        run_log_path: str,
        error_message: str,
    ) -> None:
        if self.notifier is None:
            return
        try:
            self.notifier.notify_job_finished(
                job,
                terminal_status=terminal_status,
                result_status=result_status,
                run_label=run_label,
                pr_url=pr_url,
                target_summary_path=target_summary_path,
                run_log_path=run_log_path,
                error_message=error_message,
            )
        except Exception as exc:
            print(f"[WARN] 发送钉钉结束通知失败: {exc}")

    def run_forever(self) -> None:
        """Keep polling the database queue forever."""

        print(
            f"[INFO] DingTalk worker started "
            f"(poll={self.poll_interval_seconds}s, timeout={self.stale_running_timeout_seconds}s)"
        )
        while True:
            consumed = self.run_once()
            if not consumed:
                time.sleep(self.poll_interval_seconds)


def parse_args() -> argparse.Namespace:
    """Parse worker CLI arguments."""

    parser = argparse.ArgumentParser(description="运行 DingTalk 手动任务后台 Worker")
    parser.add_argument("--run-once", action="store_true", help="只拉取并执行一个任务")
    parser.add_argument(
        "--poll-interval-seconds",
        type=int,
        default=5,
        help="轮询间隔秒数（默认 5）",
    )
    parser.add_argument(
        "--stale-running-timeout-seconds",
        type=int,
        default=7200,
        help="running 超时阈值秒数（默认 7200）",
    )
    return parser.parse_args()


def main() -> None:
    """CLI entry for the serial DingTalk worker."""

    args = parse_args()
    job_store = create_job_store_from_env()
    if job_store is None:
        raise RuntimeError("未配置 DB_*，无法启动 DingTalk worker")

    worker = DingTalkWorker(
        job_store=job_store,
        job_runner=JobRunner(db_client=job_store.db_client),
        notifier=create_dingtalk_job_notifier_from_env(),
        poll_interval_seconds=args.poll_interval_seconds,
        stale_running_timeout_seconds=args.stale_running_timeout_seconds,
    )
    if args.run_once:
        worker.run_once()
        return
    worker.run_forever()


if __name__ == "__main__":
    main()
