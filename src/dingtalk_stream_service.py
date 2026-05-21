"""Real DingTalk Stream-mode bridge for manual-trigger gateway callbacks."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pi_sonar_agent.core.project_env import read_project_env
from pi_sonar_agent.dingtalk_gateway import (
    DingTalkGateway,
    DingTalkGatewayResult,
    create_dingtalk_gateway_from_env,
)
from pi_sonar_agent.integrations.dingtalk_bot import build_confirmation_card, extract_card_action

DINGTALK_STREAM_CHATBOT_TOPIC = "/v1.0/im/bot/messages/get"
DINGTALK_STREAM_CARD_CALLBACK_TOPIC = "/v1.0/card/instances/callback"


@dataclass(frozen=True)
class DingTalkStreamServiceConfig:
    """Configuration for one real DingTalk Stream service."""

    client_id: str
    client_secret: str
    targets_path: str = "data/targets.json"
    chatbot_topic: str = DINGTALK_STREAM_CHATBOT_TOPIC
    card_callback_topic: str = DINGTALK_STREAM_CARD_CALLBACK_TOPIC
    confirmation_card_template_id: str = ""
    reconnect_delay_seconds: int = 5


class DingTalkStreamService:
    """Thin bridge from DingTalk Stream callbacks into the existing gateway."""

    def __init__(
        self,
        *,
        gateway: DingTalkGateway,
        config: DingTalkStreamServiceConfig,
        stream_module: Any | None = None,
    ) -> None:
        self.gateway = gateway
        self.config = config
        self._stream_module = stream_module

    def start_forever(self) -> None:
        """Start the DingTalk stream client forever."""

        reconnect_delay_seconds = max(int(self.config.reconnect_delay_seconds or 5), 1)
        while True:
            try:
                client = self.build_client()
                client.start_forever()
                print(
                    "[WARN] DingTalk Stream 连接已退出，"
                    f"{reconnect_delay_seconds}s 后尝试重新连接..."
                )
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                print(
                    "[WARN] DingTalk Stream 运行异常，"
                    f"{reconnect_delay_seconds}s 后尝试重新连接: {exc}"
                )
            time.sleep(reconnect_delay_seconds)

    def build_client(self) -> Any:
        """Build one registered DingTalk stream client instance."""

        stream_module = self._load_stream_module()
        credential = stream_module.Credential(
            self.config.client_id,
            self.config.client_secret,
        )
        client = stream_module.DingTalkStreamClient(credential)
        client.register_callback_handler(
            self.config.chatbot_topic,
            self._build_chatbot_handler(stream_module),
        )
        client.register_callback_handler(
            self.config.card_callback_topic,
            self._build_card_callback_handler(stream_module),
        )
        return client

    def _build_chatbot_handler(self, stream_module: Any) -> Any:
        service = self
        ack_ok, ack_message = _resolve_ack(stream_module)

        class _GatewayChatbotHandler(stream_module.ChatbotHandler):
            async def process(self, callback: Any) -> tuple[Any, str]:
                payload = _normalize_callback_payload(getattr(callback, "data", callback))
                result = service.gateway.handle_event_payload(payload)
                service._reply_to_chatbot_callback(self, stream_module, payload, result)
                return ack_ok, ack_message

        return _GatewayChatbotHandler()

    def _build_card_callback_handler(self, stream_module: Any) -> Any:
        service = self
        ack_ok, ack_message = _resolve_ack(stream_module)
        callback_handler_type = getattr(stream_module, "CallbackHandler", object)

        class _GatewayCardCallbackHandler(callback_handler_type):
            async def process(self, callback: Any) -> tuple[Any, str]:
                payload = _normalize_callback_payload(getattr(callback, "data", callback))
                result = service.gateway.handle_confirmation_payload(payload)
                service._sync_confirmation_card(self, stream_module, payload, result)
                return ack_ok, ack_message

        return _GatewayCardCallbackHandler()

    def _reply_to_chatbot_callback(
        self,
        handler: Any,
        stream_module: Any,
        payload: dict[str, Any],
        result: DingTalkGatewayResult,
    ) -> None:
        chatbot_message_type = getattr(stream_module, "ChatbotMessage", None)
        if chatbot_message_type is None:
            return
        from_dict = getattr(chatbot_message_type, "from_dict", None)
        if not callable(from_dict):
            return
        incoming_message = from_dict(payload)
        card_sent = False
        reply_card = getattr(result, "reply_card", None)
        if reply_card:
            card_instance_id = self._deliver_confirmation_card(
                handler=handler,
                stream_module=stream_module,
                incoming_message=incoming_message,
                reply_card=reply_card,
            )
            if str(card_instance_id or "").strip():
                card_sent = True
                if result.job_id:
                    self.gateway.job_store.attach_confirmation_card_instance(
                        job_id=result.job_id,
                        confirmation_card_instance_id=str(card_instance_id).strip(),
                    )
        reply_text = str(result.reply_text or "").strip()
        if card_sent or not reply_text:
            return
        if hasattr(handler, "reply_text"):
            handler.reply_text(reply_text, incoming_message)
            return
        if hasattr(handler, "reply_markdown"):
            handler.reply_markdown(
                "Sonar 手动修复任务",
                reply_text,
                incoming_message,
            )

    def _deliver_confirmation_card(
        self,
        *,
        handler: Any,
        stream_module: Any,
        incoming_message: Any,
        reply_card: dict[str, Any],
    ) -> str:
        card_replier_type = getattr(stream_module, "CardReplier", None)
        dingtalk_client = getattr(handler, "dingtalk_client", None)
        if (
            self.config.confirmation_card_template_id
            and card_replier_type is not None
            and dingtalk_client is not None
        ):
            try:
                card_replier = card_replier_type(dingtalk_client, incoming_message)
                card_instance_id = card_replier.create_and_deliver_card(
                    self.config.confirmation_card_template_id,
                    _build_confirmation_template_card_data(reply_card),
                    callback_type="STREAM",
                    support_forward=False,
                )
            except Exception:
                card_instance_id = ""
            if str(card_instance_id or "").strip():
                return str(card_instance_id).strip()
        return ""

    def _sync_confirmation_card(
        self,
        handler: Any,
        stream_module: Any,
        payload: dict[str, Any],
        result: DingTalkGatewayResult,
    ) -> None:
        action = extract_card_action(payload)
        card_instance_id = str(action.card_instance_id or "").strip()
        if not card_instance_id and result.job_id:
            job = self.gateway.job_store.get_job(result.job_id)
            card_instance_id = str(
                getattr(job, "confirmation_card_instance_id", "") or ""
            ).strip()
        if not card_instance_id:
            return

        job = self.gateway.job_store.get_job(result.job_id) if result.job_id else None
        if job is None:
            job = self.gateway.job_store.get_job_by_confirmation_card_instance_id(card_instance_id)
        if job is None:
            return

        card_replier_type = getattr(stream_module, "CardReplier", None)
        dingtalk_client = getattr(handler, "dingtalk_client", None)
        if (
            not self.config.confirmation_card_template_id
            or card_replier_type is None
            or dingtalk_client is None
        ):
            return

        try:
            card_replier = card_replier_type(
                dingtalk_client,
                _build_synthetic_chat_message(job),
            )
            card_replier.put_card_data(
                card_instance_id,
                _build_confirmation_template_card_data(
                    build_confirmation_card(
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
                    ),
                    status=_map_confirmation_status(result.status),
                ),
            )
        except Exception:
            return

    def _load_stream_module(self) -> Any:
        if self._stream_module is not None:
            return self._stream_module
        try:
            return importlib.import_module("dingtalk_stream")
        except Exception as exc:  # pragma: no cover - import path depends on runtime env
            raise RuntimeError(
                "未安装 dingtalk_stream，请先执行 `pip install dingtalk-stream` 再启动真实钉钉 Stream 服务。"
            ) from exc


def create_dingtalk_stream_service_from_env(
    *,
    targets_path: str | Path = "data/targets.json",
    stream_module: Any | None = None,
) -> DingTalkStreamService | None:
    """Create one stream service from repository env and existing gateway config."""

    env = read_project_env()
    client_id = (
        env.get("DINGTALK_STREAM_CLIENT_ID", "").strip()
        or env.get("DINGTALK_APPKEY", "").strip()
    )
    client_secret = (
        env.get("DINGTALK_STREAM_CLIENT_SECRET", "").strip()
        or env.get("DINGTALK_APPSECRET", "").strip()
    )
    if not client_id or not client_secret:
        return None
    gateway = create_dingtalk_gateway_from_env(targets_path=targets_path)
    if gateway is None:
        return None
    return DingTalkStreamService(
        gateway=gateway,
        config=DingTalkStreamServiceConfig(
            client_id=client_id,
            client_secret=client_secret,
            targets_path=str(targets_path),
            confirmation_card_template_id=env.get(
                "DINGTALK_CONFIRMATION_CARD_TEMPLATE_ID", ""
            ).strip(),
            reconnect_delay_seconds=int(
                env.get("DINGTALK_STREAM_RECONNECT_DELAY_SECONDS", "5") or 5
            ),
        ),
        stream_module=stream_module,
    )


def parse_args() -> argparse.Namespace:
    """Parse CLI args for the real DingTalk Stream bridge."""

    parser = argparse.ArgumentParser(description="启动真实钉钉 Stream 手动触发桥接服务")
    parser.add_argument(
        "--targets-file",
        default="data/targets.json",
        help="targets.json 路径（默认 data/targets.json）",
    )
    return parser.parse_args()


def _configure_stdio_for_services() -> None:
    """Force UTF-8 stdio so NSSM redirected logs stay readable on Windows."""

    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                continue


def main() -> None:
    """CLI entry for the DingTalk stream bridge."""

    _configure_stdio_for_services()
    args = parse_args()
    service = create_dingtalk_stream_service_from_env(targets_path=args.targets_file)
    if service is None:
        raise RuntimeError(
            "未配置 DingTalk Stream 凭据或 DB_*，无法启动真实钉钉 Stream 手动触发桥接服务。"
        )
    service.start_forever()


def _normalize_callback_payload(data: Any) -> dict[str, Any]:
    if isinstance(data, dict):
        return dict(data)
    text = str(data or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _resolve_ack(stream_module: Any) -> tuple[Any, str]:
    ack_message_type = getattr(stream_module, "AckMessage", None)
    if ack_message_type is None:
        return 200, "OK"
    status_ok = getattr(ack_message_type, "STATUS_OK", 200)
    return status_ok, "OK"


def _build_dingtalk_confirmation_card(card_payload: dict[str, Any]) -> dict[str, Any]:
    title = str(card_payload.get("title", "") or "确认执行 Sonar 自动修复")
    fields = card_payload.get("fields") if isinstance(card_payload.get("fields"), list) else []
    actions = card_payload.get("actions") if isinstance(card_payload.get("actions"), list) else []
    markdown_lines: list[str] = []
    for field in fields:
        if not isinstance(field, dict):
            continue
        label = str(field.get("label", "") or "").strip()
        value = str(field.get("value", "") or "").strip()
        if label and value:
            markdown_lines.append(f"**{label}**：{value}")
    contents: list[dict[str, Any]] = [
        {
            "type": "markdown",
            "text": "\n".join(markdown_lines) or "请确认是否执行本次 Sonar 自动修复任务。",
            "id": "confirmation_summary",
        }
    ]
    rendered_actions: list[dict[str, Any]] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        action_id = str(action.get("action", "") or "").strip()
        label = str(action.get("label", "") or "").strip()
        style = str(action.get("style", "") or "").strip().lower()
        if not action_id or not label:
            continue
        rendered_actions.append(
            {
                "type": "button",
                "label": {
                    "type": "text",
                    "text": label,
                    "id": f"label_{action_id}",
                },
                "actionType": "request",
                "status": "warning" if style == "danger" else "primary",
                "id": action_id,
            }
        )
    if rendered_actions:
        contents.append(
            {
                "type": "action",
                "actions": rendered_actions,
                "id": "confirmation_actions",
            }
        )
    return {
        "config": {
            "autoLayout": True,
            "enableForward": False,
        },
        "header": {
            "title": {
                "type": "text",
                "text": title,
            }
        },
        "contents": contents,
    }


def _build_confirmation_template_card_data(
    card_payload: dict[str, Any],
    *,
    status: str = "待处理",
) -> dict[str, str]:
    title = str(card_payload.get("title", "") or "确认执行 Sonar 自动修复").strip()
    field_map = {
        str(field.get("label", "") or "").strip(): str(field.get("value", "") or "").strip()
        for field in (card_payload.get("fields") or [])
        if isinstance(field, dict)
    }
    repository = field_map.get("仓库", "(未指定)")
    author = field_map.get("作者", "(未指定)")
    project_key = field_map.get("项目", "(未指定)")
    base_branch = field_map.get("基线分支", "(未指定)")
    issue_keys = field_map.get("issue_keys", "(未指定)")
    skip_issue_keys = field_map.get("skip_issue_keys", "(未指定)")
    max_issues = field_map.get("max_issues", "(未指定)")
    trigger_user = field_map.get("触发人", "(未知)")
    job_id = field_map.get("任务编号", str(card_payload.get("job_id", "") or "").strip())
    return {
        "title": title,
        "type": f"{repository} / {base_branch}",
        "amount": str(max_issues or "(未指定)"),
        "reason": (
            f"任务编号：{job_id}\n"
            f"作者：{author}\n"
            f"项目：{project_key}\n"
            f"issue_keys：{issue_keys}\n"
            f"skip_issue_keys：{skip_issue_keys}\n"
            f"触发人：{trigger_user}"
        ),
        "status": status,
    }


def _map_confirmation_status(status: str) -> str:
    normalized = str(status or "").strip().lower()
    if normalized in {"confirmed", "already_confirmed"}:
        return "已确认"
    if normalized in {"cancelled", "already_cancelled"}:
        return "已取消"
    if normalized == "cannot_cancel":
        return "不可取消"
    if normalized == "cannot_confirm":
        return "无法确认"
    if normalized == "unauthorized":
        return "无权限"
    if normalized == "invalid_confirmation":
        return "确认无效"
    return "待处理"


def _build_synthetic_chat_message(job: Any) -> Any:
    return type(
        "SyntheticChatMessage",
        (),
        {
            "sender_id": str(getattr(job, "trigger_user_id", "") or "unknown-user"),
            "sender_corp_id": "",
            "conversation_id": str(getattr(job, "conversation_id", "") or "unknown-conversation"),
            "message_id": str(getattr(job, "job_id", "") or "unknown-job"),
            "conversation_type": (
                "2"
                if str(getattr(job, "conversation_type", "") or "").lower() == "group_chat"
                else "1"
            ),
            "sender_staff_id": str(getattr(job, "trigger_user_id", "") or ""),
            "sender_nick": str(getattr(job, "trigger_user_name", "") or ""),
            "hosting_context": None,
        },
    )()


__all__ = [
    "DINGTALK_STREAM_CARD_CALLBACK_TOPIC",
    "DINGTALK_STREAM_CHATBOT_TOPIC",
    "DingTalkStreamService",
    "DingTalkStreamServiceConfig",
    "create_dingtalk_stream_service_from_env",
    "_build_confirmation_template_card_data",
    "_build_dingtalk_confirmation_card",
]


if __name__ == "__main__":
    main()
