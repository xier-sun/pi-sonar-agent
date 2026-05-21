from __future__ import annotations

from pi_sonar_agent.integrations.dingtalk_bot import (
    build_job_status_reply,
    build_recent_job_reply,
    build_rerun_pre_confirmation_reply,
    build_confirmation_callback_reply,
    build_confirmation_card,
    build_pre_confirmation_reply,
    DingTalkConfirmJobCommand,
    DingTalkCancelJobCommand,
    DingTalkCancelCurrentJobCommand,
    DingTalkRerunJobCommand,
    DingTalkShowJobCommand,
    DingTalkShowRecentJobCommand,
    extract_card_action,
    extract_incoming_message,
    parse_dingtalk_command,
    build_fix_follow_up_reply,
)


def test_extract_incoming_message_handles_common_payload_shape() -> None:
    payload = {
        "msgId": "MSG-1",
        "senderStaffId": "staff-1",
        "senderNick": "Alice",
        "conversationType": "group_chat",
        "conversationId": "conv-1",
        "text": {"content": "修复 BI alice@example.com"},
    }

    message = extract_incoming_message(payload)

    assert message.message_id == "MSG-1"
    assert message.sender_staff_id == "staff-1"
    assert message.sender_nick == "Alice"
    assert message.conversation_type == "group_chat"
    assert message.conversation_id == "conv-1"
    assert message.text == "修复 BI alice@example.com"


def test_parse_dingtalk_command_supports_optional_fix_args() -> None:
    result = parse_dingtalk_command(
        "修复 BI alice@example.com "
        "base_branch=develop issue_keys=a,b skip_issue_keys=c，d "
        "max_issues=5 reviewer_email=rv@example.com project_key=sonar-bi"
    )

    assert result.parse_status == "parsed"
    assert result.command is not None
    assert result.command.repository == "BI"
    assert result.command.author == "alice@example.com"
    assert result.command.base_branch == "develop"
    assert result.command.issue_keys == ("a", "b")
    assert result.command.skip_issue_keys == ("c", "d")
    assert result.command.max_issues == 5
    assert result.command.reviewer_email == "rv@example.com"
    assert result.command.project_key == "sonar-bi"


def test_parse_dingtalk_command_supports_partial_fix_prompt_and_option_only_updates() -> None:
    partial = parse_dingtalk_command("修复 BI")
    option_only = parse_dingtalk_command("max_issues=3 reviewer_email=rv@example.com")

    assert partial.parse_status == "parsed"
    assert partial.command is not None
    assert partial.command.repository == "BI"
    assert partial.command.author == ""
    assert option_only.parse_status == "parsed"
    assert option_only.command is not None
    assert option_only.command.max_issues == 3
    assert option_only.command.reviewer_email == "rv@example.com"


def test_parse_dingtalk_command_supports_json_style_issue_key_lists() -> None:
    result = parse_dingtalk_command(
        '修复 BI alice@example.com issue_keys=["a","b"] '
        'skip_issue_keys=["c"] max_issues=1'
    )

    assert result.parse_status == "parsed"
    assert result.command is not None
    assert result.command.issue_keys == ("a", "b")
    assert result.command.skip_issue_keys == ("c",)


def test_parse_dingtalk_command_supports_bracketed_plain_issue_key_lists() -> None:
    result = parse_dingtalk_command(
        "修复 BI alice@example.com "
        "skip_issue_keys=[f00c0210-75dd-4924-b17e-9dd05c948a08]"
    )

    assert result.parse_status == "parsed"
    assert result.command is not None
    assert result.command.skip_issue_keys == ("f00c0210-75dd-4924-b17e-9dd05c948a08",)


def test_parse_dingtalk_command_tolerates_half_written_bracketed_issue_key_lists() -> None:
    result = parse_dingtalk_command(
        '修复 BI alice@example.com '
        'skip_issue_keys=["f00c0210-75dd-4924-b17e-9dd05c948a08"'
    )

    assert result.parse_status == "parsed"
    assert result.command is not None
    assert result.command.skip_issue_keys == ("f00c0210-75dd-4924-b17e-9dd05c948a08",)


def test_parse_dingtalk_command_rejects_duplicate_or_bad_args() -> None:
    duplicate = parse_dingtalk_command(
        "修复 BI alice@example.com issue_keys=a issue_keys=b"
    )
    bad_int = parse_dingtalk_command(
        "修复 BI alice@example.com max_issues=abc"
    )

    assert duplicate.parse_status == "parse_error"
    assert "重复参数" in duplicate.parse_error
    assert bad_int.parse_status == "parse_error"
    assert "max_issues 必须是整数" in bad_int.parse_error


def test_parse_dingtalk_command_supports_cancel_job() -> None:
    result = parse_dingtalk_command("取消任务 JOB-20260518-001")

    assert result.parse_status == "parsed"
    assert result.command_type == "cancel_job"
    assert isinstance(result.command, DingTalkCancelJobCommand)
    assert result.command.job_id == "JOB-20260518-001"


def test_parse_dingtalk_command_supports_cancel_current_job_aliases() -> None:
    for text in ("停止修复", "取消修复", "停止", "取消"):
        result = parse_dingtalk_command(text)
        assert result.parse_status == "parsed"
        assert result.command_type == "cancel_current_job"
        assert isinstance(result.command, DingTalkCancelCurrentJobCommand)


def test_parse_dingtalk_command_supports_confirm_job() -> None:
    result = parse_dingtalk_command("确认任务 JOB-20260518-001")

    assert result.parse_status == "parsed"
    assert result.command_type == "confirm_job"
    assert isinstance(result.command, DingTalkConfirmJobCommand)
    assert result.command.job_id == "JOB-20260518-001"


def test_parse_dingtalk_command_supports_show_job_and_recent_and_rerun() -> None:
    show_job = parse_dingtalk_command("查看任务 JOB-20260518-002")
    show_recent = parse_dingtalk_command("查看我最近一次修复")
    rerun = parse_dingtalk_command("重跑任务 JOB-20260518-003")

    assert show_job.parse_status == "parsed"
    assert isinstance(show_job.command, DingTalkShowJobCommand)
    assert show_job.command.job_id == "JOB-20260518-002"
    assert show_recent.parse_status == "parsed"
    assert isinstance(show_recent.command, DingTalkShowRecentJobCommand)
    assert rerun.parse_status == "parsed"
    assert isinstance(rerun.command, DingTalkRerunJobCommand)
    assert rerun.command.job_id == "JOB-20260518-003"


def test_build_pre_confirmation_reply_contains_key_fields() -> None:
    reply = build_pre_confirmation_reply(
        job_id="JOB-1",
        repository="BI",
        author="alice@example.com",
        project_key="sonar-bi",
        base_branch="develop",
        issue_keys=("i1", "i2"),
        skip_issue_keys=("i3",),
        max_issues=5,
        reviewer_email="rv@example.com",
        dingtalk_userid="ding-user",
    )

    assert "JOB-1" in reply
    assert "仓库: BI" in reply
    assert "作者: alice@example.com" in reply
    assert "项目: sonar-bi" in reply
    assert "issue_keys: i1, i2" in reply
    assert "审阅者账号: rv@example.com" in reply
    assert "下一步操作：" in reply
    assert "确认任务 JOB-1" in reply
    assert "取消任务 JOB-1" in reply


def test_build_pre_confirmation_reply_hides_card_hint_when_card_is_disabled() -> None:
    reply = build_pre_confirmation_reply(
        job_id="JOB-2",
        repository="BI",
        author="alice@example.com",
        project_key="sonar-bi",
        base_branch="develop",
        issue_keys=(),
        skip_issue_keys=(),
        max_issues=5,
        confirmation_card_enabled=False,
    )

    assert "下一步操作：" in reply
    assert "1. 开始执行：发送 确认任务 JOB-2" in reply
    assert "2. 放弃本次：发送 取消任务 JOB-2" in reply
    assert "请在确认卡片中点击" not in reply


def test_build_job_status_and_recent_and_rerun_replies_include_follow_up_actions() -> None:
    status_reply = build_job_status_reply(
        job_id="JOB-9",
        status="awaiting_confirmation",
        repository="BI",
        author="alice@example.com",
        base_branch="develop",
        issue_keys=("i1",),
        skip_issue_keys=("i2",),
    )
    recent_reply = build_recent_job_reply(sender_nick="Alice", reply_text=status_reply)
    rerun_reply = build_rerun_pre_confirmation_reply(
        original_job_id="JOB-1",
        new_job_id="JOB-2",
        repository="BI",
        author="alice@example.com",
        project_key="sonar-bi",
        base_branch="develop",
        issue_keys=("i1",),
        skip_issue_keys=("i2",),
        max_issues=5,
    )

    assert "确认任务 JOB-9" in status_reply
    assert "Alice" in recent_reply
    assert "历史任务 JOB-1" in rerun_reply
    assert "任务编号: JOB-2" in rerun_reply


def test_build_confirmation_card_contains_fields_and_actions() -> None:
    card = build_confirmation_card(
        job_id="JOB-1",
        repository="BI",
        author="alice@example.com",
        project_key="sonar-bi",
        base_branch="develop",
        issue_keys=("i1", "i2"),
        skip_issue_keys=("i3",),
        max_issues=5,
        reviewer_email="rv@example.com",
        trigger_user_name="Alice",
        confirmation_token="token-1",
    )

    assert card["card_type"] == "sonar_manual_fix_confirmation"
    assert card["title"] == "确认执行 Sonar 自动修复"
    assert any(field["label"] == "仓库" and field["value"] == "BI" for field in card["fields"])
    assert any(field["label"] == "审阅者账号" and field["value"] == "rv@example.com" for field in card["fields"])
    assert card["actions"][0]["action"] == "confirm_fix_job"
    assert card["actions"][1]["action"] == "cancel_fix_job"


def test_build_fix_follow_up_reply_points_to_missing_fields() -> None:
    reply = build_fix_follow_up_reply(
        repository="BI",
        active_job_id="JOB-1",
        missing_fields=("author",),
    )

    assert "当前仓库: BI" in reply
    assert "当前待确认任务: JOB-1" in reply
    assert "还缺: author" in reply


def test_extract_card_action_handles_common_callback_payload() -> None:
    action = extract_card_action(
        {
            "messageId": "MSG-9",
            "senderStaffId": "staff-1",
            "senderNick": "Alice",
            "conversationId": "conv-1",
            "cardPrivateData": {
                "action": "confirm_fix_job",
                "job_id": "JOB-1",
                "confirmation_token": "token-1",
            },
        }
    )

    assert action.action == "confirm_fix_job"
    assert action.job_id == "JOB-1"
    assert action.confirmation_token == "token-1"
    assert action.message_id == "MSG-9"
    assert action.sender_staff_id == "staff-1"


def test_extract_card_action_handles_card_instance_callback_payload() -> None:
    action = extract_card_action(
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

    assert action.action == "confirm_fix_job"
    assert action.card_instance_id == "card-1"
    assert action.sender_staff_id == "staff-1"


def test_extract_card_action_handles_stream_template_action_payload() -> None:
    action = extract_card_action(
        {
            "outTrackId": "card-2",
            "userId": "staff-2",
            "content": {
                "action": "accept",
            },
        }
    )

    assert action.action == "accept"
    assert action.card_instance_id == "card-2"
    assert action.sender_staff_id == "staff-2"


def test_build_confirmation_callback_reply_returns_terminal_hints() -> None:
    confirmed = build_confirmation_callback_reply(
        status="confirmed",
        job_id="JOB-1",
        repository="BI",
        author="alice@example.com",
    )
    cancelled = build_confirmation_callback_reply(
        status="cancelled",
        job_id="JOB-1",
        repository="BI",
        author="alice@example.com",
    )

    assert "已确认执行" in confirmed
    assert "已取消执行请求" in cancelled
