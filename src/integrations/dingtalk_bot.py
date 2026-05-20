"""DingTalk bot message parsing and normalized inbound event helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


SUPPORTED_COMMAND_KEYS = frozenset(
    {
        "base_branch",
        "issue_keys",
        "skip_issue_keys",
        "max_issues",
        "reviewer_email",
        "dingtalk_userid",
        "project_key",
    }
)


@dataclass(frozen=True)
class DingTalkIncomingMessage:
    """Normalized inbound DingTalk text message."""

    message_id: str
    sender_staff_id: str
    sender_nick: str
    conversation_type: str
    conversation_id: str
    text: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DingTalkFixCommand:
    """Parsed `修复 ...` command payload."""

    repository: str
    author: str
    base_branch: str = ""
    issue_keys: tuple[str, ...] = ()
    skip_issue_keys: tuple[str, ...] = ()
    max_issues: int | None = None
    reviewer_email: str = ""
    dingtalk_userid: str = ""
    project_key: str = ""


@dataclass(frozen=True)
class DingTalkCancelJobCommand:
    """Parsed `取消任务 ...` command payload."""

    job_id: str


@dataclass(frozen=True)
class DingTalkConfirmJobCommand:
    """Parsed `确认任务 ...` command payload."""

    job_id: str


@dataclass(frozen=True)
class DingTalkShowJobCommand:
    """Parsed `查看任务 ...` command payload."""

    job_id: str


@dataclass(frozen=True)
class DingTalkShowRecentJobCommand:
    """Parsed `查看我最近一次修复` command payload."""


@dataclass(frozen=True)
class DingTalkRerunJobCommand:
    """Parsed `重跑任务 ...` command payload."""

    job_id: str


@dataclass(frozen=True)
class DingTalkCommandParseResult:
    """Result of parsing one DingTalk text command."""

    parse_status: str
    command_type: str
    raw_text: str
    command: (
        DingTalkFixCommand
        | DingTalkCancelJobCommand
        | DingTalkConfirmJobCommand
        | DingTalkShowJobCommand
        | DingTalkShowRecentJobCommand
        | DingTalkRerunJobCommand
        | None
    ) = None
    parse_error: str = ""


@dataclass(frozen=True)
class DingTalkCardAction:
    """Normalized callback payload emitted by one confirmation card action."""

    action: str
    job_id: str
    confirmation_token: str
    card_instance_id: str = ""
    message_id: str = ""
    sender_staff_id: str = ""
    sender_nick: str = ""
    conversation_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


def extract_incoming_message(payload: dict[str, Any]) -> DingTalkIncomingMessage:
    """Normalize a DingTalk event payload into one text message object."""

    body = dict(payload or {})
    message = _nested_dict(body, "message")
    sender = _nested_dict(body, "sender")
    conversation = _nested_dict(body, "conversation")

    message_id = _first_non_empty(
        body.get("msgId"),
        body.get("messageId"),
        body.get("eventId"),
        message.get("msgId"),
        message.get("messageId"),
        message.get("eventId"),
    )
    sender_staff_id = _first_non_empty(
        body.get("senderStaffId"),
        body.get("senderId"),
        body.get("staffId"),
        sender.get("staffId"),
        sender.get("senderStaffId"),
        sender.get("id"),
    )
    sender_nick = _first_non_empty(
        body.get("senderNick"),
        body.get("senderName"),
        sender.get("nick"),
        sender.get("name"),
        sender.get("senderNick"),
    )
    conversation_type = _first_non_empty(
        body.get("conversationType"),
        body.get("chatType"),
        conversation.get("conversationType"),
        conversation.get("chatType"),
        conversation.get("type"),
    )
    conversation_id = _first_non_empty(
        body.get("conversationId"),
        body.get("chatbotConversationId"),
        conversation.get("conversationId"),
        conversation.get("id"),
    )
    text = _extract_message_text(body, message)

    return DingTalkIncomingMessage(
        message_id=message_id,
        sender_staff_id=sender_staff_id,
        sender_nick=sender_nick,
        conversation_type=conversation_type,
        conversation_id=conversation_id,
        text=text,
        payload=body,
    )


def parse_dingtalk_command(text: str) -> DingTalkCommandParseResult:
    """Parse supported manual-trigger commands from DingTalk text."""

    raw_text = str(text or "").strip()
    if not raw_text:
        return DingTalkCommandParseResult(
            parse_status="ignored",
            command_type="empty",
            raw_text=raw_text,
            parse_error="空消息，忽略处理",
        )

    if raw_text.startswith("确认任务 ") or raw_text.startswith("确认 "):
        tokens = raw_text.split()
        if len(tokens) != 2 or not tokens[1].strip():
            return DingTalkCommandParseResult(
                parse_status="parse_error",
                command_type="confirm_job",
                raw_text=raw_text,
                parse_error="确认命令格式应为：确认任务 <job_id>",
            )
        return DingTalkCommandParseResult(
            parse_status="parsed",
            command_type="confirm_job",
            raw_text=raw_text,
            command=DingTalkConfirmJobCommand(job_id=tokens[1].strip()),
        )

    if raw_text.startswith("查看任务 ") or raw_text.startswith("查询任务 "):
        tokens = raw_text.split()
        if len(tokens) != 2 or not tokens[1].strip():
            return DingTalkCommandParseResult(
                parse_status="parse_error",
                command_type="show_job",
                raw_text=raw_text,
                parse_error="查看任务命令格式应为：查看任务 <job_id>",
            )
        return DingTalkCommandParseResult(
            parse_status="parsed",
            command_type="show_job",
            raw_text=raw_text,
            command=DingTalkShowJobCommand(job_id=tokens[1].strip()),
        )

    if raw_text in {"查看我最近一次修复", "查看最近一次修复", "查看我最近任务"}:
        return DingTalkCommandParseResult(
            parse_status="parsed",
            command_type="show_recent_job",
            raw_text=raw_text,
            command=DingTalkShowRecentJobCommand(),
        )

    if raw_text.startswith("重跑任务 ") or raw_text.startswith("重跑 "):
        tokens = raw_text.split()
        if len(tokens) != 2 or not tokens[1].strip():
            return DingTalkCommandParseResult(
                parse_status="parse_error",
                command_type="rerun_job",
                raw_text=raw_text,
                parse_error="重跑命令格式应为：重跑任务 <job_id>",
            )
        return DingTalkCommandParseResult(
            parse_status="parsed",
            command_type="rerun_job",
            raw_text=raw_text,
            command=DingTalkRerunJobCommand(job_id=tokens[1].strip()),
        )

    if raw_text.startswith("取消任务 ") or raw_text.startswith("取消 "):
        tokens = raw_text.split()
        if len(tokens) != 2 or not tokens[1].strip():
            return DingTalkCommandParseResult(
                parse_status="parse_error",
                command_type="cancel_job",
                raw_text=raw_text,
                parse_error="取消命令格式应为：取消任务 <job_id>",
            )
        return DingTalkCommandParseResult(
            parse_status="parsed",
            command_type="cancel_job",
            raw_text=raw_text,
            command=DingTalkCancelJobCommand(job_id=tokens[1].strip()),
        )

    if not raw_text.startswith("修复 "):
        return DingTalkCommandParseResult(
            parse_status="unsupported_command",
            command_type="unsupported",
            raw_text=raw_text,
            parse_error=(
                "当前支持：修复 <repository> <author> ... / 确认任务 <job_id> / "
                "取消任务 <job_id> / 查看任务 <job_id> / 查看我最近一次修复 / 重跑任务 <job_id>"
            ),
        )

    tokens = raw_text.split()
    if len(tokens) < 3:
        return DingTalkCommandParseResult(
            parse_status="parse_error",
            command_type="fix",
            raw_text=raw_text,
            parse_error="修复命令至少需要 repository 和 author，例如：修复 BI alice@example.com",
        )

    repository = tokens[1].strip()
    author = tokens[2].strip()
    if not repository or not author:
        return DingTalkCommandParseResult(
            parse_status="parse_error",
            command_type="fix",
            raw_text=raw_text,
            parse_error="repository 或 author 为空，无法执行修复",
        )

    options: dict[str, str] = {}
    for token in tokens[3:]:
        if "=" not in token:
            return DingTalkCommandParseResult(
                parse_status="parse_error",
                command_type="fix",
                raw_text=raw_text,
                parse_error=f"无法识别的参数片段：{token}",
            )
        key, value = token.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key not in SUPPORTED_COMMAND_KEYS:
            return DingTalkCommandParseResult(
                parse_status="parse_error",
                command_type="fix",
                raw_text=raw_text,
                parse_error=f"不支持的参数：{key}",
            )
        if key in options:
            return DingTalkCommandParseResult(
                parse_status="parse_error",
                command_type="fix",
                raw_text=raw_text,
                parse_error=f"重复参数：{key}",
            )
        options[key] = value

    try:
        max_issues = (
            int(options["max_issues"])
            if "max_issues" in options and options["max_issues"] != ""
            else None
        )
    except ValueError:
        return DingTalkCommandParseResult(
            parse_status="parse_error",
            command_type="fix",
            raw_text=raw_text,
            parse_error="max_issues 必须是整数",
        )
    if max_issues is not None and max_issues < 0:
        return DingTalkCommandParseResult(
            parse_status="parse_error",
            command_type="fix",
            raw_text=raw_text,
            parse_error="max_issues 不能小于 0",
        )

    command = DingTalkFixCommand(
        repository=repository,
        author=author,
        base_branch=options.get("base_branch", "").strip(),
        issue_keys=_split_csv(options.get("issue_keys", "")),
        skip_issue_keys=_split_csv(options.get("skip_issue_keys", "")),
        max_issues=max_issues,
        reviewer_email=options.get("reviewer_email", "").strip(),
        dingtalk_userid=options.get("dingtalk_userid", "").strip(),
        project_key=options.get("project_key", "").strip(),
    )
    return DingTalkCommandParseResult(
        parse_status="parsed",
        command_type="fix",
        raw_text=raw_text,
        command=command,
    )


def build_pre_confirmation_reply(
    *,
    job_id: str,
    repository: str,
    author: str,
    project_key: str,
    base_branch: str,
    issue_keys: tuple[str, ...],
    skip_issue_keys: tuple[str, ...],
    max_issues: int,
) -> str:
    """Build a user-facing pre-confirmation preview text."""

    issue_keys_text = ", ".join(issue_keys) if issue_keys else "(未指定)"
    skip_issue_keys_text = ", ".join(skip_issue_keys) if skip_issue_keys else "(未指定)"
    return (
        "已收到修复请求，任务已进入待确认状态。\n"
        f"任务编号: {job_id}\n"
        f"仓库: {repository}\n"
        f"作者: {author}\n"
        f"项目: {project_key}\n"
        f"基线分支: {base_branch}\n"
        f"issue_keys: {issue_keys_text}\n"
        f"skip_issue_keys: {skip_issue_keys_text}\n"
        f"max_issues: {max_issues}\n"
        f"请在确认卡片中点击“确认执行”或“取消”；如果当前通道未展示卡片，可发送：确认任务 {job_id} / 取消任务 {job_id}"
    )


def build_job_status_reply(
    *,
    job_id: str,
    status: str,
    repository: str,
    author: str,
    base_branch: str,
    issue_keys: tuple[str, ...],
    skip_issue_keys: tuple[str, ...],
    run_label: str = "",
    result_status: str = "",
    pr_url: str = "",
    target_summary_path: str = "",
    run_log_path: str = "",
    error_message: str = "",
) -> str:
    """Build a concise task status summary for one existing job."""

    issue_keys_text = ", ".join(issue_keys) if issue_keys else "(未指定)"
    skip_issue_keys_text = ", ".join(skip_issue_keys) if skip_issue_keys else "(未指定)"
    lines = [
        "任务状态",
        f"任务编号: {job_id}",
        f"当前状态: {status}",
        f"仓库: {repository}",
        f"作者: {author}",
        f"基线分支: {base_branch}",
        f"issue_keys: {issue_keys_text}",
        f"skip_issue_keys: {skip_issue_keys_text}",
    ]
    if run_label:
        lines.append(f"run_label: {run_label}")
    if result_status:
        lines.append(f"执行结果: {result_status}")
    if pr_url:
        lines.append(f"PR 链接: {pr_url}")
    if target_summary_path:
        lines.append(f"运行摘要: {target_summary_path}")
    if run_log_path:
        lines.append(f"运行日志: {run_log_path}")
    if error_message:
        lines.append(f"附加说明: {error_message}")
    if status == "awaiting_confirmation":
        lines.append(f"可继续操作: 确认任务 {job_id} / 取消任务 {job_id}")
    if status in {"succeeded", "partial", "failed", "cancelled", "timeout"}:
        lines.append(f"如需再次执行，可发送：重跑任务 {job_id}")
    return "\n".join(lines)


def build_recent_job_reply(*, sender_nick: str, reply_text: str) -> str:
    """Build one wrapper reply for recent job queries."""

    prefix = (
        f"{sender_nick}，这是你最近一次修复任务："
        if str(sender_nick or "").strip()
        else "这是你最近一次修复任务："
    )
    return f"{prefix}\n{reply_text}"


def build_rerun_pre_confirmation_reply(
    *,
    original_job_id: str,
    new_job_id: str,
    repository: str,
    author: str,
    project_key: str,
    base_branch: str,
    issue_keys: tuple[str, ...],
    skip_issue_keys: tuple[str, ...],
    max_issues: int,
) -> str:
    """Build one pre-confirmation preview for a rerun request."""

    preview = build_pre_confirmation_reply(
        job_id=new_job_id,
        repository=repository,
        author=author,
        project_key=project_key,
        base_branch=base_branch,
        issue_keys=issue_keys,
        skip_issue_keys=skip_issue_keys,
        max_issues=max_issues,
    )
    return f"已基于历史任务 {original_job_id} 创建重跑请求。\n{preview}"


def build_confirmation_card(
    *,
    job_id: str,
    repository: str,
    author: str,
    project_key: str,
    base_branch: str,
    issue_keys: tuple[str, ...],
    skip_issue_keys: tuple[str, ...],
    max_issues: int,
    trigger_user_name: str = "",
    confirmation_token: str,
) -> dict[str, Any]:
    """Build one transport-agnostic confirmation card payload."""

    issue_keys_text = ", ".join(issue_keys) if issue_keys else "(未指定)"
    skip_issue_keys_text = ", ".join(skip_issue_keys) if skip_issue_keys else "(未指定)"
    return {
        "card_type": "sonar_manual_fix_confirmation",
        "title": "确认执行 Sonar 自动修复",
        "job_id": job_id,
        "fields": [
            {"label": "任务编号", "value": job_id},
            {"label": "仓库", "value": repository},
            {"label": "作者", "value": author},
            {"label": "项目", "value": project_key or "(未指定)"},
            {"label": "基线分支", "value": base_branch},
            {"label": "issue_keys", "value": issue_keys_text},
            {"label": "skip_issue_keys", "value": skip_issue_keys_text},
            {"label": "max_issues", "value": str(max_issues)},
            {"label": "触发人", "value": trigger_user_name or "(未知)"},
        ],
        "actions": [
            {
                "action": "confirm_fix_job",
                "label": "确认执行",
                "style": "primary",
                "job_id": job_id,
                "confirmation_token": confirmation_token,
            },
            {
                "action": "cancel_fix_job",
                "label": "取消",
                "style": "danger",
                "job_id": job_id,
                "confirmation_token": confirmation_token,
            },
        ],
    }


def extract_card_action(payload: dict[str, Any]) -> DingTalkCardAction:
    """Normalize one DingTalk card callback payload."""

    body = dict(payload or {})
    callback = _nested_dict(body, "callback")
    content = _merge_dicts(
        _coerce_json_object(body.get("content")),
        _nested_dict(body, "content"),
        _coerce_json_object(callback.get("content")),
        _nested_dict(callback, "content"),
    )
    card_private_data = _merge_dicts(
        _coerce_json_object(content.get("cardPrivateData")),
        _nested_dict(content, "cardPrivateData"),
    )
    action_ids = _normalize_action_ids(
        card_private_data.get("actionIds"),
        content.get("actionIds"),
        body.get("actionIds"),
    )
    action_data = _merge_dicts(
        _coerce_json_object(body.get("value")),
        _coerce_json_object(body.get("actionValue")),
        _coerce_json_object(body.get("cardPrivateData")),
        _nested_dict(body, "value"),
        _nested_dict(body, "actionValue"),
        _nested_dict(body, "cardPrivateData"),
        _nested_dict(callback, "value"),
        _nested_dict(callback, "actionValue"),
        _nested_dict(callback, "cardPrivateData"),
    )
    sender = _nested_dict(body, "sender")
    conversation = _nested_dict(body, "conversation")

    action = _first_non_empty(
        body.get("action"),
        body.get("actionKey"),
        body.get("callbackAction"),
        callback.get("action"),
        callback.get("actionKey"),
        content.get("action"),
        content.get("actionKey"),
        action_data.get("action"),
        action_data.get("actionKey"),
        action_ids[0] if action_ids else "",
    )
    job_id = _first_non_empty(
        body.get("jobId"),
        callback.get("jobId"),
        action_data.get("job_id"),
        action_data.get("jobId"),
        card_private_data.get("job_id"),
        card_private_data.get("jobId"),
    )
    confirmation_token = _first_non_empty(
        body.get("confirmationToken"),
        callback.get("confirmationToken"),
        action_data.get("confirmation_token"),
        action_data.get("confirmationToken"),
        card_private_data.get("confirmation_token"),
        card_private_data.get("confirmationToken"),
    )
    card_instance_id = _first_non_empty(
        body.get("outTrackId"),
        callback.get("outTrackId"),
        content.get("outTrackId"),
        action_data.get("outTrackId"),
    )
    message_id = _first_non_empty(
        body.get("msgId"),
        body.get("messageId"),
        callback.get("msgId"),
        callback.get("messageId"),
    )
    sender_staff_id = _first_non_empty(
        body.get("senderStaffId"),
        body.get("senderId"),
        body.get("userId"),
        sender.get("staffId"),
        sender.get("id"),
    )
    sender_nick = _first_non_empty(
        body.get("senderNick"),
        body.get("senderName"),
        sender.get("nick"),
        sender.get("name"),
    )
    conversation_id = _first_non_empty(
        body.get("conversationId"),
        body.get("chatbotConversationId"),
        conversation.get("conversationId"),
        conversation.get("id"),
    )

    return DingTalkCardAction(
        action=action,
        job_id=job_id,
        confirmation_token=confirmation_token,
        card_instance_id=card_instance_id,
        message_id=message_id,
        sender_staff_id=sender_staff_id,
        sender_nick=sender_nick,
        conversation_id=conversation_id,
        payload=body,
    )


def build_confirmation_callback_reply(
    *,
    status: str,
    job_id: str,
    repository: str,
    author: str,
    run_label: str = "",
) -> str:
    """Build a concise reply after confirm/cancel callback handling."""

    base = f"任务编号: {job_id}\n仓库: {repository}\n作者: {author}"
    if status == "confirmed":
        run_line = f"\nrun_label: {run_label}" if run_label else ""
        return f"已确认执行，任务已进入队列。\n{base}{run_line}"
    if status == "already_confirmed":
        return f"该任务已确认，无需重复操作。\n{base}"
    if status == "cancelled":
        return f"已取消执行请求。\n{base}"
    if status == "already_cancelled":
        return f"该任务已经取消，无需重复操作。\n{base}"
    if status == "cannot_cancel":
        return f"任务已进入执行或终态，当前不能取消。\n{base}"
    if status == "invalid_confirmation":
        return "确认信息无效或已过期，请重新发起修复命令。"
    return f"当前操作未生效。\n{base}"


def _extract_message_text(payload: dict[str, Any], message: dict[str, Any]) -> str:
    candidates: list[object] = [
        _nested_dict(payload, "text").get("content"),
        _nested_dict(payload, "text").get("text"),
        payload.get("content"),
        payload.get("text"),
        _nested_dict(payload, "content").get("text"),
        _nested_dict(payload, "content").get("content"),
        _nested_dict(message, "text").get("content"),
        _nested_dict(message, "text").get("text"),
        message.get("content"),
        message.get("text"),
    ]
    return _first_non_empty(*candidates)


def _split_csv(value: str) -> tuple[str, ...]:
    normalized = str(value or "").replace("，", ",")
    items = [item.strip() for item in normalized.split(",")]
    return tuple(dict.fromkeys(item for item in items if item))


def _first_non_empty(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _nested_dict(mapping: dict[str, Any], key: str) -> dict[str, Any]:
    value = mapping.get(key)
    return value if isinstance(value, dict) else {}


def _coerce_json_object(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _merge_dicts(*values: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for value in values:
        if isinstance(value, dict):
            merged.update(value)
    return merged


def _normalize_action_ids(*values: object) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        if isinstance(value, str):
            text = value.strip()
            if text:
                normalized.append(text)
            continue
        if isinstance(value, (list, tuple, set)):
            normalized.extend(str(item).strip() for item in value if str(item).strip())
    return tuple(dict.fromkeys(normalized))


__all__ = [
    "DingTalkCardAction",
    "DingTalkConfirmJobCommand",
    "DingTalkCancelJobCommand",
    "DingTalkCommandParseResult",
    "DingTalkFixCommand",
    "DingTalkIncomingMessage",
    "DingTalkRerunJobCommand",
    "DingTalkShowJobCommand",
    "DingTalkShowRecentJobCommand",
    "build_job_status_reply",
    "build_recent_job_reply",
    "build_rerun_pre_confirmation_reply",
    "build_confirmation_callback_reply",
    "build_confirmation_card",
    "build_pre_confirmation_reply",
    "extract_card_action",
    "extract_incoming_message",
    "parse_dingtalk_command",
]
