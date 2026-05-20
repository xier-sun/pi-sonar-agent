"""Minimal DingTalk message gateway for manual-trigger job creation."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pi_sonar_agent.core.dingtalk_access_policy import (
    DingTalkAccessPolicy,
    create_dingtalk_access_policy_from_env,
)
from pi_sonar_agent.core.job_store import JobStore, create_job_store_from_env
from pi_sonar_agent.integrations.dingtalk_bot import (
    build_job_status_reply,
    build_recent_job_reply,
    build_confirmation_callback_reply,
    build_confirmation_card,
    DingTalkCancelJobCommand,
    DingTalkConfirmJobCommand,
    DingTalkFixCommand,
    DingTalkIncomingMessage,
    DingTalkRerunJobCommand,
    DingTalkShowJobCommand,
    DingTalkShowRecentJobCommand,
    build_pre_confirmation_reply,
    build_rerun_pre_confirmation_reply,
    extract_card_action,
    extract_incoming_message,
    parse_dingtalk_command,
)


@dataclass(frozen=True)
class DingTalkGatewayResult:
    """Result returned after handling one inbound DingTalk event."""

    status: str
    job_id: str = ""
    reply_text: str = ""
    parse_error: str = ""
    command_recorded: bool = False
    reply_card: dict[str, Any] | None = None


class DingTalkGateway:
    """Transport-agnostic gateway that turns DingTalk messages into queued jobs."""

    def __init__(
        self,
        *,
        job_store: JobStore,
        targets_path: Path | str = "data/targets.json",
        access_policy: DingTalkAccessPolicy | None = None,
    ) -> None:
        self.job_store = job_store
        self.targets_path = Path(targets_path)
        self.access_policy = access_policy

    def handle_event_payload(self, payload: dict[str, Any]) -> DingTalkGatewayResult:
        """Handle one DingTalk event payload and create an awaiting-confirmation job."""

        incoming = extract_incoming_message(payload)
        if incoming.message_id:
            existing = self.job_store.get_command_record_by_message_id(incoming.message_id)
            if existing is not None:
                return DingTalkGatewayResult(
                    status="duplicate",
                    job_id=existing.job_id,
                    reply_text=f"检测到重复消息，已忽略。任务编号: {existing.job_id or '(未创建任务)'}",
                    command_recorded=False,
                )

        parsed = parse_dingtalk_command(incoming.text)
        if parsed.parse_status != "parsed" or parsed.command is None:
            self.job_store.record_command(
                job_id="",
                message_id=incoming.message_id,
                sender_staff_id=incoming.sender_staff_id,
                sender_nick=incoming.sender_nick,
                raw_text=incoming.text,
                parsed_command={},
                parse_status=parsed.parse_status,
                parse_error=parsed.parse_error,
            )
            return DingTalkGatewayResult(
                status=parsed.parse_status,
                reply_text=parsed.parse_error or "暂不支持当前命令",
                parse_error=parsed.parse_error,
                command_recorded=True,
            )

        if isinstance(parsed.command, DingTalkConfirmJobCommand):
            return self._handle_confirm_command(incoming, parsed.command)

        if isinstance(parsed.command, DingTalkShowJobCommand):
            return self._handle_show_job_command(incoming, parsed.command)

        if isinstance(parsed.command, DingTalkShowRecentJobCommand):
            return self._handle_show_recent_job_command(incoming)

        if isinstance(parsed.command, DingTalkRerunJobCommand):
            return self._handle_rerun_job_command(incoming, parsed.command)

        if isinstance(parsed.command, DingTalkCancelJobCommand):
            return self._handle_cancel_command(incoming, parsed.command)

        decision = self._evaluate_trigger_access(incoming)
        if not decision.allowed:
            self.job_store.record_command(
                job_id="",
                message_id=incoming.message_id,
                sender_staff_id=incoming.sender_staff_id,
                sender_nick=incoming.sender_nick,
                raw_text=incoming.text,
                parsed_command=_command_to_dict(parsed.command),
                parse_status=decision.status,
                parse_error=decision.message,
            )
            return DingTalkGatewayResult(
                status=decision.status,
                reply_text=decision.message,
                parse_error=decision.message,
                command_recorded=True,
            )

        try:
            resolved_target = resolve_fix_request_against_targets(
                parsed.command,  # type: ignore[arg-type]
                load_targets_registry(self.targets_path),
            )
        except ValueError as exc:
            self.job_store.record_command(
                job_id="",
                message_id=incoming.message_id,
                sender_staff_id=incoming.sender_staff_id,
                sender_nick=incoming.sender_nick,
                raw_text=incoming.text,
                parsed_command=_command_to_dict(parsed.command),
                parse_status="target_unresolved",
                parse_error=str(exc),
            )
            return DingTalkGatewayResult(
                status="target_unresolved",
                reply_text=str(exc),
                parse_error=str(exc),
                command_recorded=True,
            )

        resolved_dingtalk_userid = _first_non_empty(
            str(resolved_target.get("dingtalk_userid", "") or ""),
            incoming.sender_staff_id,
        )
        resolved_target["dingtalk_userid"] = resolved_dingtalk_userid
        job = self.job_store.create_job(
            repository=str(resolved_target.get("repository", "") or ""),
            project_key=str(resolved_target.get("project_key", "") or ""),
            author=str(resolved_target.get("author", "") or ""),
            base_branch=str(resolved_target.get("base_branch", "") or "develop"),
            issue_keys=tuple(resolved_target.get("issue_keys", ()) or ()),
            skip_issue_keys=tuple(resolved_target.get("skip_issue_keys", ()) or ()),
            max_issues=int(resolved_target.get("max_issues", 0) or 0),
            reviewer_email=str(resolved_target.get("reviewer_email", "") or ""),
            dingtalk_userid=resolved_dingtalk_userid,
            target_payload=resolved_target,
            trigger_source="dingtalk_bot",
            trigger_user_id=incoming.sender_staff_id,
            trigger_user_name=incoming.sender_nick,
            conversation_type=incoming.conversation_type,
            conversation_id=incoming.conversation_id,
            await_confirmation=True,
        )
        self.job_store.record_command(
            job_id=job.job_id,
            message_id=incoming.message_id,
            sender_staff_id=incoming.sender_staff_id,
            sender_nick=incoming.sender_nick,
            raw_text=incoming.text,
            parsed_command=_command_to_dict(parsed.command),
            parse_status="parsed",
            parse_error="",
        )
        reply = build_pre_confirmation_reply(
            job_id=job.job_id,
            repository=job.repository,
            author=job.author,
            project_key=job.project_key,
            base_branch=job.base_branch,
            issue_keys=job.issue_keys,
            skip_issue_keys=job.skip_issue_keys,
            max_issues=job.max_issues,
        )
        reply_card = build_confirmation_card(
            job_id=job.job_id,
            repository=job.repository,
            author=job.author,
            project_key=job.project_key,
            base_branch=job.base_branch,
            issue_keys=job.issue_keys,
            skip_issue_keys=job.skip_issue_keys,
            max_issues=job.max_issues,
            trigger_user_name=job.trigger_user_name,
            confirmation_token=job.confirmation_token,
        )
        return DingTalkGatewayResult(
            status="awaiting_confirmation",
            job_id=job.job_id,
            reply_text=reply,
            command_recorded=True,
            reply_card=reply_card,
        )

    def handle_confirmation_payload(self, payload: dict[str, Any]) -> DingTalkGatewayResult:
        """Handle one confirmation/cancel callback payload from DingTalk."""

        action = extract_card_action(payload)
        normalized_action = _normalize_card_action(action.action)
        if not normalized_action:
            return DingTalkGatewayResult(
                status="invalid_confirmation",
                reply_text=build_confirmation_callback_reply(
                    status="invalid_confirmation",
                    job_id="",
                    repository="",
                    author="",
                ),
            )

        job = None
        if action.job_id:
            job = self.job_store.get_job(action.job_id)
        if job is None and action.card_instance_id:
            job = self.job_store.get_job_by_confirmation_card_instance_id(action.card_instance_id)
        if job is None:
            return DingTalkGatewayResult(
                status="invalid_confirmation",
                job_id=action.job_id,
                reply_text=build_confirmation_callback_reply(
                    status="invalid_confirmation",
                    job_id=action.job_id,
                    repository="",
                    author="",
                ),
            )
        if action.confirmation_token and job.confirmation_token != action.confirmation_token:
            return DingTalkGatewayResult(
                status="invalid_confirmation",
                job_id=job.job_id,
                reply_text=build_confirmation_callback_reply(
                    status="invalid_confirmation",
                    job_id=job.job_id,
                    repository=job.repository,
                    author=job.author,
                ),
            )
        if normalized_action == "confirm":
            if action.sender_staff_id and not self._can_confirm_job(job, action.sender_staff_id):
                return DingTalkGatewayResult(
                    status="unauthorized",
                    job_id=job.job_id,
                    reply_text="当前用户没有确认该任务的权限。",
                )
            return self._handle_confirm(job.job_id, job.confirmation_token)
        if action.sender_staff_id and not self._can_cancel_job(job, action.sender_staff_id):
            return DingTalkGatewayResult(
                status="unauthorized",
                job_id=job.job_id,
                reply_text="当前用户没有取消该任务的权限。",
            )
        return self._handle_cancel(job.job_id, job.confirmation_token)

    def _handle_show_job_command(
        self,
        incoming: DingTalkIncomingMessage,
        command: DingTalkShowJobCommand,
    ) -> DingTalkGatewayResult:
        decision = self._evaluate_source_only_access(incoming)
        if not decision.allowed:
            self.job_store.record_command(
                job_id="",
                message_id=incoming.message_id,
                sender_staff_id=incoming.sender_staff_id,
                sender_nick=incoming.sender_nick,
                raw_text=incoming.text,
                parsed_command={"job_id": command.job_id},
                parse_status=decision.status,
                parse_error=decision.message,
            )
            return DingTalkGatewayResult(
                status=decision.status,
                reply_text=decision.message,
                parse_error=decision.message,
                command_recorded=True,
            )

        job = self.job_store.get_job(command.job_id)
        if job is None:
            self.job_store.record_command(
                job_id="",
                message_id=incoming.message_id,
                sender_staff_id=incoming.sender_staff_id,
                sender_nick=incoming.sender_nick,
                raw_text=incoming.text,
                parsed_command={"job_id": command.job_id},
                parse_status="job_not_found",
                parse_error=f"未找到任务：{command.job_id}",
            )
            return DingTalkGatewayResult(
                status="job_not_found",
                reply_text=f"未找到任务：{command.job_id}",
                parse_error=f"未找到任务：{command.job_id}",
                command_recorded=True,
            )

        if not self._can_view_job(job, incoming.sender_staff_id):
            self.job_store.record_command(
                job_id=job.job_id,
                message_id=incoming.message_id,
                sender_staff_id=incoming.sender_staff_id,
                sender_nick=incoming.sender_nick,
                raw_text=incoming.text,
                parsed_command={"job_id": command.job_id},
                parse_status="unauthorized",
                parse_error="当前用户没有查看该任务的权限。",
            )
            return DingTalkGatewayResult(
                status="unauthorized",
                job_id=job.job_id,
                reply_text="当前用户没有查看该任务的权限。",
                parse_error="当前用户没有查看该任务的权限。",
                command_recorded=True,
            )

        self.job_store.record_command(
            job_id=job.job_id,
            message_id=incoming.message_id,
            sender_staff_id=incoming.sender_staff_id,
            sender_nick=incoming.sender_nick,
            raw_text=incoming.text,
            parsed_command={"job_id": command.job_id},
            parse_status="parsed",
            parse_error="",
        )
        return DingTalkGatewayResult(
            status="job_status",
            job_id=job.job_id,
            reply_text=build_job_status_reply(
                job_id=job.job_id,
                status=job.status,
                repository=job.repository,
                author=job.author,
                base_branch=job.base_branch,
                issue_keys=job.issue_keys,
                skip_issue_keys=job.skip_issue_keys,
                run_label=job.run_label,
                result_status=job.result_status,
                pr_url=job.pr_url,
                target_summary_path=job.target_summary_path,
                run_log_path=job.run_log_path,
                error_message=job.error_message,
            ),
            command_recorded=True,
        )

    def _handle_show_recent_job_command(
        self,
        incoming: DingTalkIncomingMessage,
    ) -> DingTalkGatewayResult:
        decision = self._evaluate_source_only_access(incoming)
        if not decision.allowed:
            self.job_store.record_command(
                job_id="",
                message_id=incoming.message_id,
                sender_staff_id=incoming.sender_staff_id,
                sender_nick=incoming.sender_nick,
                raw_text=incoming.text,
                parsed_command={"command": "show_recent_job"},
                parse_status=decision.status,
                parse_error=decision.message,
            )
            return DingTalkGatewayResult(
                status=decision.status,
                reply_text=decision.message,
                parse_error=decision.message,
                command_recorded=True,
            )

        job = self.job_store.get_latest_job_for_user(incoming.sender_staff_id)
        if job is None:
            self.job_store.record_command(
                job_id="",
                message_id=incoming.message_id,
                sender_staff_id=incoming.sender_staff_id,
                sender_nick=incoming.sender_nick,
                raw_text=incoming.text,
                parsed_command={"command": "show_recent_job"},
                parse_status="job_not_found",
                parse_error="当前用户暂无历史修复任务。",
            )
            return DingTalkGatewayResult(
                status="job_not_found",
                reply_text="当前用户暂无历史修复任务。",
                parse_error="当前用户暂无历史修复任务。",
                command_recorded=True,
            )

        self.job_store.record_command(
            job_id=job.job_id,
            message_id=incoming.message_id,
            sender_staff_id=incoming.sender_staff_id,
            sender_nick=incoming.sender_nick,
            raw_text=incoming.text,
            parsed_command={"command": "show_recent_job"},
            parse_status="parsed",
            parse_error="",
        )
        return DingTalkGatewayResult(
            status="job_status",
            job_id=job.job_id,
            reply_text=build_recent_job_reply(
                sender_nick=incoming.sender_nick,
                reply_text=build_job_status_reply(
                    job_id=job.job_id,
                    status=job.status,
                    repository=job.repository,
                    author=job.author,
                    base_branch=job.base_branch,
                    issue_keys=job.issue_keys,
                    skip_issue_keys=job.skip_issue_keys,
                    run_label=job.run_label,
                    result_status=job.result_status,
                    pr_url=job.pr_url,
                    target_summary_path=job.target_summary_path,
                    run_log_path=job.run_log_path,
                    error_message=job.error_message,
                ),
            ),
            command_recorded=True,
        )

    def _handle_rerun_job_command(
        self,
        incoming: DingTalkIncomingMessage,
        command: DingTalkRerunJobCommand,
    ) -> DingTalkGatewayResult:
        decision = self._evaluate_trigger_access(incoming)
        if not decision.allowed:
            self.job_store.record_command(
                job_id="",
                message_id=incoming.message_id,
                sender_staff_id=incoming.sender_staff_id,
                sender_nick=incoming.sender_nick,
                raw_text=incoming.text,
                parsed_command={"job_id": command.job_id},
                parse_status=decision.status,
                parse_error=decision.message,
            )
            return DingTalkGatewayResult(
                status=decision.status,
                reply_text=decision.message,
                parse_error=decision.message,
                command_recorded=True,
            )

        original_job = self.job_store.get_job(command.job_id)
        if original_job is None:
            self.job_store.record_command(
                job_id="",
                message_id=incoming.message_id,
                sender_staff_id=incoming.sender_staff_id,
                sender_nick=incoming.sender_nick,
                raw_text=incoming.text,
                parsed_command={"job_id": command.job_id},
                parse_status="job_not_found",
                parse_error=f"未找到任务：{command.job_id}",
            )
            return DingTalkGatewayResult(
                status="job_not_found",
                reply_text=f"未找到任务：{command.job_id}",
                parse_error=f"未找到任务：{command.job_id}",
                command_recorded=True,
            )

        if not self._can_rerun_job(original_job, incoming.sender_staff_id):
            self.job_store.record_command(
                job_id=original_job.job_id,
                message_id=incoming.message_id,
                sender_staff_id=incoming.sender_staff_id,
                sender_nick=incoming.sender_nick,
                raw_text=incoming.text,
                parsed_command={"job_id": command.job_id},
                parse_status="unauthorized",
                parse_error="当前用户没有重跑该任务的权限。",
            )
            return DingTalkGatewayResult(
                status="unauthorized",
                job_id=original_job.job_id,
                reply_text="当前用户没有重跑该任务的权限。",
                parse_error="当前用户没有重跑该任务的权限。",
                command_recorded=True,
            )

        if original_job.status in {"awaiting_confirmation", "queued", "running"}:
            self.job_store.record_command(
                job_id=original_job.job_id,
                message_id=incoming.message_id,
                sender_staff_id=incoming.sender_staff_id,
                sender_nick=incoming.sender_nick,
                raw_text=incoming.text,
                parsed_command={"job_id": command.job_id},
                parse_status="cannot_rerun",
                parse_error="原任务仍在进行中，请等待其结束后再重跑。",
            )
            return DingTalkGatewayResult(
                status="cannot_rerun",
                job_id=original_job.job_id,
                reply_text="原任务仍在进行中，请等待其结束后再重跑。",
                parse_error="原任务仍在进行中，请等待其结束后再重跑。",
                command_recorded=True,
            )

        rerun_job = self.job_store.create_rerun_job(
            original_job,
            trigger_user_id=incoming.sender_staff_id,
            trigger_user_name=incoming.sender_nick,
            conversation_type=incoming.conversation_type,
            conversation_id=incoming.conversation_id,
        )
        self.job_store.record_command(
            job_id=rerun_job.job_id,
            message_id=incoming.message_id,
            sender_staff_id=incoming.sender_staff_id,
            sender_nick=incoming.sender_nick,
            raw_text=incoming.text,
            parsed_command={"job_id": command.job_id, "rerun_job_id": rerun_job.job_id},
            parse_status="parsed",
            parse_error="",
        )
        return DingTalkGatewayResult(
            status="awaiting_confirmation",
            job_id=rerun_job.job_id,
            reply_text=build_rerun_pre_confirmation_reply(
                original_job_id=original_job.job_id,
                new_job_id=rerun_job.job_id,
                repository=rerun_job.repository,
                author=rerun_job.author,
                project_key=rerun_job.project_key,
                base_branch=rerun_job.base_branch,
                issue_keys=rerun_job.issue_keys,
                skip_issue_keys=rerun_job.skip_issue_keys,
                max_issues=rerun_job.max_issues,
            ),
            command_recorded=True,
            reply_card=build_confirmation_card(
                job_id=rerun_job.job_id,
                repository=rerun_job.repository,
                author=rerun_job.author,
                project_key=rerun_job.project_key,
                base_branch=rerun_job.base_branch,
                issue_keys=rerun_job.issue_keys,
                skip_issue_keys=rerun_job.skip_issue_keys,
                max_issues=rerun_job.max_issues,
                trigger_user_name=rerun_job.trigger_user_name,
                confirmation_token=rerun_job.confirmation_token,
            ),
        )

    def _handle_cancel_command(
        self,
        incoming: DingTalkIncomingMessage,
        command: DingTalkCancelJobCommand,
    ) -> DingTalkGatewayResult:
        decision = self._evaluate_source_only_access(incoming)
        if not decision.allowed:
            self.job_store.record_command(
                job_id="",
                message_id=incoming.message_id,
                sender_staff_id=incoming.sender_staff_id,
                sender_nick=incoming.sender_nick,
                raw_text=incoming.text,
                parsed_command={"job_id": command.job_id},
                parse_status=decision.status,
                parse_error=decision.message,
            )
            return DingTalkGatewayResult(
                status=decision.status,
                reply_text=decision.message,
                parse_error=decision.message,
                command_recorded=True,
            )

        job = self.job_store.get_job(command.job_id)
        if job is None:
            self.job_store.record_command(
                job_id="",
                message_id=incoming.message_id,
                sender_staff_id=incoming.sender_staff_id,
                sender_nick=incoming.sender_nick,
                raw_text=incoming.text,
                parsed_command={"job_id": command.job_id},
                parse_status="job_not_found",
                parse_error=f"未找到任务：{command.job_id}",
            )
            return DingTalkGatewayResult(
                status="job_not_found",
                reply_text=f"未找到任务：{command.job_id}",
                parse_error=f"未找到任务：{command.job_id}",
                command_recorded=True,
            )

        if not self._can_cancel_job(job, incoming.sender_staff_id):
            self.job_store.record_command(
                job_id=job.job_id,
                message_id=incoming.message_id,
                sender_staff_id=incoming.sender_staff_id,
                sender_nick=incoming.sender_nick,
                raw_text=incoming.text,
                parsed_command={"job_id": command.job_id},
                parse_status="unauthorized",
                parse_error="当前用户没有取消该任务的权限。",
            )
            return DingTalkGatewayResult(
                status="unauthorized",
                job_id=job.job_id,
                reply_text="当前用户没有取消该任务的权限。",
                parse_error="当前用户没有取消该任务的权限。",
                command_recorded=True,
            )

        if job.status == "cancelled":
            self.job_store.record_command(
                job_id=job.job_id,
                message_id=incoming.message_id,
                sender_staff_id=incoming.sender_staff_id,
                sender_nick=incoming.sender_nick,
                raw_text=incoming.text,
                parsed_command={"job_id": command.job_id},
                parse_status="already_cancelled",
                parse_error="",
            )
            return DingTalkGatewayResult(
                status="already_cancelled",
                job_id=job.job_id,
                reply_text=f"任务 {job.job_id} 已经取消，无需重复操作。",
                command_recorded=True,
            )

        if job.status not in {"awaiting_confirmation", "queued"}:
            self.job_store.record_command(
                job_id=job.job_id,
                message_id=incoming.message_id,
                sender_staff_id=incoming.sender_staff_id,
                sender_nick=incoming.sender_nick,
                raw_text=incoming.text,
                parsed_command={"job_id": command.job_id},
                parse_status="cannot_cancel",
                parse_error="任务已进入执行或终态，当前不能取消。",
            )
            return DingTalkGatewayResult(
                status="cannot_cancel",
                job_id=job.job_id,
                reply_text=f"任务 {job.job_id} 已进入执行或终态，当前不能取消。",
                parse_error="任务已进入执行或终态，当前不能取消。",
                command_recorded=True,
            )

        cancelled = self.job_store.cancel_job_by_job_id(job_id=job.job_id)
        self.job_store.record_command(
            job_id=job.job_id,
            message_id=incoming.message_id,
            sender_staff_id=incoming.sender_staff_id,
            sender_nick=incoming.sender_nick,
            raw_text=incoming.text,
            parsed_command={"job_id": command.job_id},
            parse_status="parsed",
            parse_error="",
        )
        if cancelled is None:
            return DingTalkGatewayResult(
                status="cannot_cancel",
                job_id=job.job_id,
                reply_text=f"任务 {job.job_id} 当前不能取消。",
                parse_error=f"任务 {job.job_id} 当前不能取消。",
                command_recorded=True,
            )
        return DingTalkGatewayResult(
            status="cancelled",
            job_id=cancelled.job_id,
            reply_text=f"已取消任务 {cancelled.job_id}。",
            command_recorded=True,
        )

    def _handle_confirm_command(
        self,
        incoming: DingTalkIncomingMessage,
        command: DingTalkConfirmJobCommand,
    ) -> DingTalkGatewayResult:
        decision = self._evaluate_source_only_access(incoming)
        if not decision.allowed:
            self.job_store.record_command(
                job_id="",
                message_id=incoming.message_id,
                sender_staff_id=incoming.sender_staff_id,
                sender_nick=incoming.sender_nick,
                raw_text=incoming.text,
                parsed_command={"job_id": command.job_id},
                parse_status=decision.status,
                parse_error=decision.message,
            )
            return DingTalkGatewayResult(
                status=decision.status,
                reply_text=decision.message,
                parse_error=decision.message,
                command_recorded=True,
            )

        job = self.job_store.get_job(command.job_id)
        if job is None:
            self.job_store.record_command(
                job_id="",
                message_id=incoming.message_id,
                sender_staff_id=incoming.sender_staff_id,
                sender_nick=incoming.sender_nick,
                raw_text=incoming.text,
                parsed_command={"job_id": command.job_id},
                parse_status="job_not_found",
                parse_error=f"未找到任务：{command.job_id}",
            )
            return DingTalkGatewayResult(
                status="job_not_found",
                reply_text=f"未找到任务：{command.job_id}",
                parse_error=f"未找到任务：{command.job_id}",
                command_recorded=True,
            )

        if not self._can_confirm_job(job, incoming.sender_staff_id):
            self.job_store.record_command(
                job_id=job.job_id,
                message_id=incoming.message_id,
                sender_staff_id=incoming.sender_staff_id,
                sender_nick=incoming.sender_nick,
                raw_text=incoming.text,
                parsed_command={"job_id": command.job_id},
                parse_status="unauthorized",
                parse_error="当前用户没有确认该任务的权限。",
            )
            return DingTalkGatewayResult(
                status="unauthorized",
                job_id=job.job_id,
                reply_text="当前用户没有确认该任务的权限。",
                parse_error="当前用户没有确认该任务的权限。",
                command_recorded=True,
            )

        if job.status == "queued":
            self.job_store.record_command(
                job_id=job.job_id,
                message_id=incoming.message_id,
                sender_staff_id=incoming.sender_staff_id,
                sender_nick=incoming.sender_nick,
                raw_text=incoming.text,
                parsed_command={"job_id": command.job_id},
                parse_status="already_confirmed",
                parse_error="",
            )
            return DingTalkGatewayResult(
                status="already_confirmed",
                job_id=job.job_id,
                reply_text=f"任务 {job.job_id} 已确认，无需重复操作。",
                command_recorded=True,
            )

        if job.status != "awaiting_confirmation":
            self.job_store.record_command(
                job_id=job.job_id,
                message_id=incoming.message_id,
                sender_staff_id=incoming.sender_staff_id,
                sender_nick=incoming.sender_nick,
                raw_text=incoming.text,
                parsed_command={"job_id": command.job_id},
                parse_status="cannot_confirm",
                parse_error="任务当前不处于待确认状态，无法确认。",
            )
            return DingTalkGatewayResult(
                status="cannot_confirm",
                job_id=job.job_id,
                reply_text=f"任务 {job.job_id} 当前不处于待确认状态，无法确认。",
                parse_error="任务当前不处于待确认状态，无法确认。",
                command_recorded=True,
            )

        confirmed = self.job_store.confirm_job_by_job_id(job_id=job.job_id)
        self.job_store.record_command(
            job_id=job.job_id,
            message_id=incoming.message_id,
            sender_staff_id=incoming.sender_staff_id,
            sender_nick=incoming.sender_nick,
            raw_text=incoming.text,
            parsed_command={"job_id": command.job_id},
            parse_status="parsed",
            parse_error="",
        )
        if confirmed is None:
            return DingTalkGatewayResult(
                status="cannot_confirm",
                job_id=job.job_id,
                reply_text=f"任务 {job.job_id} 当前无法确认。",
                parse_error=f"任务 {job.job_id} 当前无法确认。",
                command_recorded=True,
            )
        return DingTalkGatewayResult(
            status="confirmed",
            job_id=confirmed.job_id,
            reply_text=f"已确认任务 {confirmed.job_id}，任务已进入队列。",
            command_recorded=True,
        )

    def _handle_confirm(self, job_id: str, confirmation_token: str) -> DingTalkGatewayResult:
        job = self.job_store.get_job(job_id)
        if job is None or job.confirmation_token != confirmation_token:
            return DingTalkGatewayResult(
                status="invalid_confirmation",
                job_id=job_id,
                reply_text=build_confirmation_callback_reply(
                    status="invalid_confirmation",
                    job_id=job_id,
                    repository="",
                    author="",
                ),
            )
        if job.status == "queued":
            return DingTalkGatewayResult(
                status="already_confirmed",
                job_id=job.job_id,
                reply_text=build_confirmation_callback_reply(
                    status="already_confirmed",
                    job_id=job.job_id,
                    repository=job.repository,
                    author=job.author,
                    run_label=job.run_label,
                ),
            )
        if job.status != "awaiting_confirmation":
            return DingTalkGatewayResult(
                status="invalid_confirmation",
                job_id=job.job_id,
                reply_text=build_confirmation_callback_reply(
                    status="invalid_confirmation",
                    job_id=job.job_id,
                    repository=job.repository,
                    author=job.author,
                ),
            )
        confirmed = self.job_store.confirm_job(
            job_id=job.job_id,
            confirmation_token=confirmation_token,
        )
        if confirmed is None:
            return DingTalkGatewayResult(
                status="invalid_confirmation",
                job_id=job.job_id,
                reply_text=build_confirmation_callback_reply(
                    status="invalid_confirmation",
                    job_id=job.job_id,
                    repository=job.repository,
                    author=job.author,
                ),
            )
        return DingTalkGatewayResult(
            status="confirmed",
            job_id=confirmed.job_id,
            reply_text=build_confirmation_callback_reply(
                status="confirmed",
                job_id=confirmed.job_id,
                repository=confirmed.repository,
                author=confirmed.author,
                run_label=confirmed.run_label,
            ),
        )

    def _handle_cancel(self, job_id: str, confirmation_token: str) -> DingTalkGatewayResult:
        job = self.job_store.get_job(job_id)
        if job is None or job.confirmation_token != confirmation_token:
            return DingTalkGatewayResult(
                status="invalid_confirmation",
                job_id=job_id,
                reply_text=build_confirmation_callback_reply(
                    status="invalid_confirmation",
                    job_id=job_id,
                    repository="",
                    author="",
                ),
            )
        if job.status == "cancelled":
            return DingTalkGatewayResult(
                status="already_cancelled",
                job_id=job.job_id,
                reply_text=build_confirmation_callback_reply(
                    status="already_cancelled",
                    job_id=job.job_id,
                    repository=job.repository,
                    author=job.author,
                ),
            )
        if job.status not in {"awaiting_confirmation", "queued"}:
            return DingTalkGatewayResult(
                status="cannot_cancel",
                job_id=job.job_id,
                reply_text=build_confirmation_callback_reply(
                    status="cannot_cancel",
                    job_id=job.job_id,
                    repository=job.repository,
                    author=job.author,
                ),
            )
        cancelled = self.job_store.cancel_job(
            job_id=job.job_id,
            confirmation_token=confirmation_token,
        )
        if cancelled is None:
            return DingTalkGatewayResult(
                status="invalid_confirmation",
                job_id=job.job_id,
                reply_text=build_confirmation_callback_reply(
                    status="invalid_confirmation",
                    job_id=job.job_id,
                    repository=job.repository,
                    author=job.author,
                ),
            )
        return DingTalkGatewayResult(
            status="cancelled",
            job_id=cancelled.job_id,
            reply_text=build_confirmation_callback_reply(
                status="cancelled",
                job_id=cancelled.job_id,
                repository=cancelled.repository,
                author=cancelled.author,
            ),
        )

    def _evaluate_trigger_access(self, incoming: DingTalkIncomingMessage) -> _AccessDecision:
        if self.access_policy is None:
            return _allowed_decision()
        decision = self.access_policy.evaluate_trigger(
            job_store=self.job_store,
            sender_staff_id=incoming.sender_staff_id,
            conversation_id=incoming.conversation_id,
        )
        return _AccessDecision(decision.allowed, decision.status, decision.message)

    def _evaluate_source_only_access(self, incoming: DingTalkIncomingMessage) -> _AccessDecision:
        if self.access_policy is None:
            return _allowed_decision()
        if (
            self.access_policy.allowed_staff_ids
            and incoming.sender_staff_id not in self.access_policy.allowed_staff_ids
        ):
            return _deny_decision(
                "unauthorized",
                "当前用户不在钉钉触发白名单中，无法执行该操作。",
            )
        if (
            self.access_policy.allowed_conversation_ids
            and incoming.conversation_id not in self.access_policy.allowed_conversation_ids
        ):
            return _deny_decision(
                "unauthorized",
                "当前会话不在允许触发的钉钉群范围内，无法执行该操作。",
            )
        return _allowed_decision()

    def _can_cancel_job(self, job: Any, requester_staff_id: str) -> bool:
        if self.access_policy is None:
            return str(requester_staff_id or "").strip() == str(job.trigger_user_id or "").strip()
        return self.access_policy.can_cancel_job(
            job=job,
            requester_staff_id=requester_staff_id,
        )

    def _can_confirm_job(self, job: Any, requester_staff_id: str) -> bool:
        if self.access_policy is None:
            return str(requester_staff_id or "").strip() == str(job.trigger_user_id or "").strip()
        return self.access_policy.can_confirm_job(
            job=job,
            requester_staff_id=requester_staff_id,
        )

    def _can_view_job(self, job: Any, requester_staff_id: str) -> bool:
        if self.access_policy is None:
            return str(requester_staff_id or "").strip() == str(job.trigger_user_id or "").strip()
        return self.access_policy.can_view_job(
            job=job,
            requester_staff_id=requester_staff_id,
        )

    def _can_rerun_job(self, job: Any, requester_staff_id: str) -> bool:
        if self.access_policy is None:
            return str(requester_staff_id or "").strip() == str(job.trigger_user_id or "").strip()
        return self.access_policy.can_rerun_job(
            job=job,
            requester_staff_id=requester_staff_id,
        )


def load_targets_registry(targets_path: Path) -> list[dict[str, Any]]:
    """Load targets.json as a registry for manual-trigger resolution."""

    if not targets_path.exists():
        raise ValueError(f"未找到 targets 配置文件: {targets_path.as_posix()}")
    data = json.loads(targets_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("targets.json 根节点必须是数组")
    registry: list[dict[str, Any]] = []
    for item in data:
        if isinstance(item, dict):
            registry.append(dict(item))
    return registry


def resolve_fix_request_against_targets(
    command: DingTalkFixCommand,
    targets: list[dict[str, Any]],
) -> dict[str, Any]:
    """Resolve one fix request against targets.json and merge command overrides."""

    candidates = [
        item
        for item in targets
        if str(item.get("repository", "") or "").strip().lower() == command.repository.strip().lower()
        and str(item.get("author", "") or "").strip().lower() == command.author.strip().lower()
    ]
    if command.project_key:
        candidates = [
            item
            for item in candidates
            if str(item.get("project_key", "") or "").strip() == command.project_key
        ]
    if not candidates:
        raise ValueError(
            f"未在 targets.json 中找到匹配项：repository={command.repository}, author={command.author}"
        )

    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for item in candidates:
        grouped.setdefault(_execution_signature(item), []).append(item)
    if len(grouped) > 1:
        raise ValueError(
            "targets.json 中存在多个可执行配置，请补充 project_key 或先清理重复配置后再触发"
        )

    merged_target = dict(candidates[0])
    merged_target["reviewer_email"] = _first_non_empty(
        *(item.get("reviewer_email", "") for item in candidates)
    )
    merged_target["dingtalk_userid"] = _first_non_empty(
        command.dingtalk_userid,
        *(item.get("dingtalk_userid", "") for item in candidates),
    )
    merged_target["project_key"] = (
        command.project_key
        or str(merged_target.get("project_key", "") or "").strip()
    )
    merged_target["repository"] = command.repository.strip()
    merged_target["author"] = command.author.strip()
    merged_target["base_branch"] = command.base_branch.strip() or str(
        merged_target.get("base_branch", "") or "develop"
    ).strip() or "develop"
    merged_target["issue_keys"] = list(
        command.issue_keys or _normalize_issue_keys(merged_target.get("issue_keys"))
    )
    merged_target["skip_issue_keys"] = list(
        command.skip_issue_keys or _normalize_issue_keys(merged_target.get("skip_issue_keys"))
    )
    if command.max_issues is not None:
        merged_target["max_issues"] = int(command.max_issues)
    else:
        merged_target["max_issues"] = int(merged_target.get("max_issues", 0) or 0)
    if command.reviewer_email:
        merged_target["reviewer_email"] = command.reviewer_email
    return merged_target


def create_dingtalk_gateway_from_env(
    *,
    targets_path: Path | str = "data/targets.json",
) -> DingTalkGateway | None:
    """Create one gateway from repository DB settings."""

    job_store = create_job_store_from_env()
    if job_store is None:
        return None
    return DingTalkGateway(
        job_store=job_store,
        targets_path=targets_path,
        access_policy=create_dingtalk_access_policy_from_env(),
    )


def parse_args() -> argparse.Namespace:
    """Parse CLI args for local gateway verification."""

    parser = argparse.ArgumentParser(description="处理一条钉钉机器人文本消息并创建待确认任务")
    parser.add_argument("--event-file", help="DingTalk 事件 JSON 文件路径")
    parser.add_argument("--stdin", action="store_true", help="从标准输入读取 DingTalk 事件 JSON")
    parser.add_argument(
        "--targets-file",
        default="data/targets.json",
        help="targets.json 路径（默认 data/targets.json）",
    )
    return parser.parse_args()


def main() -> None:
    """CLI entry for local DingTalk gateway verification."""

    args = parse_args()
    if not args.event_file and not args.stdin:
        raise RuntimeError("请通过 --event-file 或 --stdin 提供 DingTalk 事件 JSON")
    if args.event_file and args.stdin:
        raise RuntimeError("--event-file 和 --stdin 不能同时使用")

    if args.stdin:
        raw = input()
    else:
        raw = Path(args.event_file).read_text(encoding="utf-8")
    payload = json.loads(raw)
    gateway = create_dingtalk_gateway_from_env(targets_path=args.targets_file)
    if gateway is None:
        raise RuntimeError("未配置 DB_*，无法创建 DingTalk gateway")
    result = gateway.handle_event_payload(payload)
    print(
        json.dumps(
            {
                "status": result.status,
                "job_id": result.job_id,
                "reply_text": result.reply_text,
                "parse_error": result.parse_error,
                "command_recorded": result.command_recorded,
                "reply_card": result.reply_card,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _execution_signature(target: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(target.get("project_key", "") or "").strip(),
        str(target.get("repository", "") or "").strip().lower(),
        str(target.get("author", "") or "").strip().lower(),
        str(target.get("base_branch", "") or "").strip().lower(),
        str(target.get("build_command", "") or "").strip(),
        str(target.get("test_command", "") or "").strip(),
        str(target.get("solution_path", "") or "").strip(),
        int(target.get("max_issues", 0) or 0),
        tuple(_normalize_issue_keys(target.get("issue_keys"))),
        tuple(_normalize_issue_keys(target.get("skip_issue_keys"))),
        bool(target.get("keep_workspace", False)),
        bool(target.get("skip_build_gate", False)),
    )


def _normalize_issue_keys(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        normalized = value.replace("，", ",")
        return tuple(dict.fromkeys(item.strip() for item in normalized.split(",") if item.strip()))
    if isinstance(value, (list, tuple, set)):
        return tuple(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))
    return ()


def _command_to_dict(
    command: (
        DingTalkFixCommand
        | DingTalkConfirmJobCommand
        | DingTalkCancelJobCommand
        | DingTalkShowJobCommand
        | DingTalkShowRecentJobCommand
        | DingTalkRerunJobCommand
    ),
) -> dict[str, Any]:
    if isinstance(command, DingTalkConfirmJobCommand):
        return {"job_id": command.job_id}
    if isinstance(command, DingTalkCancelJobCommand):
        return {"job_id": command.job_id}
    if isinstance(command, DingTalkShowJobCommand):
        return {"job_id": command.job_id}
    if isinstance(command, DingTalkShowRecentJobCommand):
        return {"command": "show_recent_job"}
    if isinstance(command, DingTalkRerunJobCommand):
        return {"job_id": command.job_id}
    return {
        "repository": command.repository,
        "author": command.author,
        "base_branch": command.base_branch,
        "issue_keys": list(command.issue_keys),
        "skip_issue_keys": list(command.skip_issue_keys),
        "max_issues": command.max_issues,
        "reviewer_email": command.reviewer_email,
        "dingtalk_userid": command.dingtalk_userid,
        "project_key": command.project_key,
    }


def _first_non_empty(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _normalize_card_action(action: str) -> str:
    normalized = str(action or "").strip().lower()
    if normalized in {"confirm", "confirm_fix_job", "confirm-job", "accept"}:
        return "confirm"
    if normalized in {"cancel", "cancel_fix_job", "cancel-job", "reject"}:
        return "cancel"
    return ""


def _allowed_decision() -> _AccessDecision:
    return _AccessDecision(True, "allowed", "")


@dataclass(frozen=True)
class _AccessDecision:
    allowed: bool
    status: str
    message: str


def _deny_decision(status: str, message: str) -> _AccessDecision:
    return _AccessDecision(False, status, message)


__all__ = [
    "DingTalkGateway",
    "DingTalkGatewayResult",
    "create_dingtalk_gateway_from_env",
    "load_targets_registry",
    "resolve_fix_request_against_targets",
]


if __name__ == "__main__":
    main()
