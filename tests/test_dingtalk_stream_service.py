from __future__ import annotations

import asyncio
from types import SimpleNamespace

import dingtalk_stream_service as stream_service_module
from dingtalk_stream_service import (
    DINGTALK_STREAM_CARD_CALLBACK_TOPIC,
    DINGTALK_STREAM_CHATBOT_TOPIC,
    DingTalkStreamService,
    DingTalkStreamServiceConfig,
    create_dingtalk_stream_service_from_env,
)


class _FakeStreamModule:
    class AckMessage:
        STATUS_OK = 200

    class CallbackHandler:
        def __init__(self) -> None:
            self.dingtalk_client = None

        def pre_start(self) -> None:
            return

    class Credential:
        def __init__(self, client_id: str, client_secret: str) -> None:
            self.client_id = client_id
            self.client_secret = client_secret

    class DingTalkStreamClient:
        def __init__(self, credential) -> None:
            self.credential = credential
            self.handlers: dict[str, object] = {}
            self.started = False

        def register_callback_handler(self, topic: str, handler: object) -> None:
            setattr(handler, "dingtalk_client", self)
            self.handlers[topic] = handler

        def start_forever(self) -> None:
            self.started = True

    class ChatbotHandler:
        def __init__(self) -> None:
            self.replies: list[tuple[str, object]] = []
            self.cards: list[tuple[dict, object]] = []
            self.dingtalk_client = None

        def reply_text(self, text: str, incoming_message: object) -> None:
            self.replies.append((text, incoming_message))

        def reply_card(self, card_data: dict, incoming_message: object, **kwargs) -> str:
            self.cards.append((card_data, incoming_message))
            return "card-1"

    class CardReplier:
        delivered_cards: list[tuple[str, dict, str, bool]] = []
        updated_cards: list[tuple[str, dict]] = []

        def __init__(self, dingtalk_client: object, incoming_message: object) -> None:
            self.dingtalk_client = dingtalk_client
            self.incoming_message = incoming_message

        def create_and_deliver_card(
            self,
            card_template_id: str,
            card_data: dict,
            callback_type: str = "STREAM",
            support_forward: bool = True,
            **kwargs,
        ) -> str:
            _FakeStreamModule.CardReplier.delivered_cards.append(
                (card_template_id, card_data, callback_type, support_forward)
            )
            return "card-1"

        def put_card_data(self, card_instance_id: str, card_data: dict, **kwargs) -> None:
            _FakeStreamModule.CardReplier.updated_cards.append((card_instance_id, card_data))

    class ChatbotMessage:
        @staticmethod
        def from_dict(data: dict) -> object:
            return SimpleNamespace(
                message=data,
                sender_id="sender-1",
                sender_corp_id="corp-1",
                conversation_id="conv-1",
                message_id="msg-1",
                conversation_type="2",
                sender_staff_id="staff-1",
                sender_nick="Alice",
                hosting_context=None,
            )


def test_stream_service_builds_client_and_registers_topics() -> None:
    gateway = SimpleNamespace(
        handle_event_payload=lambda payload: SimpleNamespace(
            reply_text="待确认任务已创建",
        ),
        handle_confirmation_payload=lambda payload: SimpleNamespace(
            reply_text="已确认执行",
        ),
    )
    service = DingTalkStreamService(
        gateway=gateway,
        config=DingTalkStreamServiceConfig(
            client_id="cid",
            client_secret="secret",
        ),
        stream_module=_FakeStreamModule,
    )

    client = service.build_client()

    assert client.credential.client_id == "cid"
    assert DINGTALK_STREAM_CHATBOT_TOPIC in client.handlers
    assert DINGTALK_STREAM_CARD_CALLBACK_TOPIC in client.handlers
    assert hasattr(client.handlers[DINGTALK_STREAM_CARD_CALLBACK_TOPIC], "pre_start")


def test_stream_service_chatbot_handler_routes_message_and_replies_text() -> None:
    seen_payloads: list[dict] = []

    def handle_event_payload(payload: dict):
        seen_payloads.append(dict(payload))
        return SimpleNamespace(reply_text="已进入待确认状态")

    service = DingTalkStreamService(
        gateway=SimpleNamespace(
            handle_event_payload=handle_event_payload,
            handle_confirmation_payload=lambda payload: None,
        ),
        config=DingTalkStreamServiceConfig(client_id="cid", client_secret="secret"),
        stream_module=_FakeStreamModule,
    )
    client = service.build_client()
    handler = client.handlers[DINGTALK_STREAM_CHATBOT_TOPIC]

    status, message = asyncio.run(
        handler.process(SimpleNamespace(data={"text": {"content": "修复 BI alice@example.com"}}))
    )

    assert status == 200
    assert message == "OK"
    assert seen_payloads[0]["text"]["content"] == "修复 BI alice@example.com"
    assert handler.replies[0][0] == "已进入待确认状态"


def test_stream_service_chatbot_handler_sends_confirmation_card_and_tracks_instance() -> None:
    _FakeStreamModule.CardReplier.delivered_cards.clear()
    _FakeStreamModule.CardReplier.updated_cards.clear()
    attached: list[tuple[str, str]] = []

    def handle_event_payload(payload: dict):
        return SimpleNamespace(
            job_id="JOB-1",
            reply_text="已进入待确认状态",
            reply_card={
                "title": "确认执行 Sonar 自动修复",
                "fields": [{"label": "任务编号", "value": "JOB-1"}],
                "actions": [
                    {"action": "confirm_fix_job", "label": "确认执行", "style": "primary"},
                    {"action": "cancel_fix_job", "label": "取消", "style": "danger"},
                ],
            },
        )

    service = DingTalkStreamService(
        gateway=SimpleNamespace(
            handle_event_payload=handle_event_payload,
            handle_confirmation_payload=lambda payload: None,
            job_store=SimpleNamespace(
                attach_confirmation_card_instance=lambda **kwargs: attached.append(
                    (kwargs["job_id"], kwargs["confirmation_card_instance_id"])
                )
            ),
        ),
        config=DingTalkStreamServiceConfig(client_id="cid", client_secret="secret"),
        stream_module=_FakeStreamModule,
    )
    client = service.build_client()
    handler = client.handlers[DINGTALK_STREAM_CHATBOT_TOPIC]

    status, message = asyncio.run(
        handler.process(SimpleNamespace(data={"text": {"content": "修复 BI alice@example.com"}}))
    )

    assert status == 200
    assert message == "OK"
    assert not _FakeStreamModule.CardReplier.delivered_cards
    assert handler.replies[0][0] == "已进入待确认状态"
    assert not attached


def test_stream_service_chatbot_handler_sends_confirmation_card_when_template_is_configured() -> None:
    _FakeStreamModule.CardReplier.delivered_cards.clear()
    _FakeStreamModule.CardReplier.updated_cards.clear()
    attached: list[tuple[str, str]] = []

    def handle_event_payload(payload: dict):
        return SimpleNamespace(
            job_id="JOB-1",
            reply_text="已进入待确认状态",
            reply_card={
                "title": "确认执行 Sonar 自动修复",
                "fields": [{"label": "任务编号", "value": "JOB-1"}],
                "actions": [
                    {"action": "confirm_fix_job", "label": "确认执行", "style": "primary"},
                    {"action": "cancel_fix_job", "label": "取消", "style": "danger"},
                ],
            },
        )

    service = DingTalkStreamService(
        gateway=SimpleNamespace(
            handle_event_payload=handle_event_payload,
            handle_confirmation_payload=lambda payload: None,
            job_store=SimpleNamespace(
                attach_confirmation_card_instance=lambda **kwargs: attached.append(
                    (kwargs["job_id"], kwargs["confirmation_card_instance_id"])
                )
            ),
        ),
        config=DingTalkStreamServiceConfig(
            client_id="cid",
            client_secret="secret",
            confirmation_card_template_id="template-1.schema",
        ),
        stream_module=_FakeStreamModule,
    )
    client = service.build_client()
    handler = client.handlers[DINGTALK_STREAM_CHATBOT_TOPIC]

    status, message = asyncio.run(
        handler.process(SimpleNamespace(data={"text": {"content": "修复 BI alice@example.com"}}))
    )

    assert status == 200
    assert message == "OK"
    assert _FakeStreamModule.CardReplier.delivered_cards[0][0] == "template-1.schema"
    assert _FakeStreamModule.CardReplier.delivered_cards[0][1]["status"] == "待处理"
    assert attached == [("JOB-1", "card-1")]
    assert not handler.replies


def test_stream_service_card_callback_handler_routes_confirmation() -> None:
    _FakeStreamModule.CardReplier.delivered_cards.clear()
    _FakeStreamModule.CardReplier.updated_cards.clear()
    seen_payloads: list[dict] = []

    def handle_confirmation_payload(payload: dict):
        seen_payloads.append(dict(payload))
        return SimpleNamespace(status="confirmed", job_id="JOB-1", reply_text="已确认执行")

    service = DingTalkStreamService(
        gateway=SimpleNamespace(
            handle_event_payload=lambda payload: None,
            handle_confirmation_payload=handle_confirmation_payload,
            job_store=SimpleNamespace(
                get_job=lambda job_id: SimpleNamespace(
                    job_id="JOB-1",
                    repository="BI",
                    author="alice@example.com",
                    project_key="sonar-bi",
                    base_branch="develop",
                    issue_keys=(),
                    skip_issue_keys=(),
                    max_issues=5,
                    trigger_user_name="Alice",
                    trigger_user_id="staff-1",
                    conversation_id="conv-1",
                    conversation_type="group_chat",
                    confirmation_token="token-1",
                    confirmation_card_instance_id="card-1",
                ),
                get_job_by_confirmation_card_instance_id=lambda card_instance_id: None,
            ),
        ),
        config=DingTalkStreamServiceConfig(
            client_id="cid",
            client_secret="secret",
            confirmation_card_template_id="template-1.schema",
        ),
        stream_module=_FakeStreamModule,
    )
    client = service.build_client()
    handler = client.handlers[DINGTALK_STREAM_CARD_CALLBACK_TOPIC]

    status, message = asyncio.run(
        handler.process(SimpleNamespace(data={"cardPrivateData": {"job_id": "JOB-1"}}))
    )

    assert status == 200
    assert message == "OK"
    assert seen_payloads[0]["cardPrivateData"]["job_id"] == "JOB-1"
    assert _FakeStreamModule.CardReplier.updated_cards[0][0] == "card-1"
    assert _FakeStreamModule.CardReplier.updated_cards[0][1]["status"] == "已确认"


def test_create_stream_service_from_env_uses_stream_or_app_credentials(monkeypatch) -> None:
    monkeypatch.setattr(
        stream_service_module,
        "read_project_env",
        lambda: {
            "DINGTALK_STREAM_CLIENT_ID": "stream-cid",
            "DINGTALK_STREAM_CLIENT_SECRET": "stream-secret",
        },
    )
    monkeypatch.setattr(
        stream_service_module,
        "create_dingtalk_gateway_from_env",
        lambda targets_path="data/targets.json": SimpleNamespace(name="gateway", targets_path=targets_path),
    )

    service = create_dingtalk_stream_service_from_env(stream_module=_FakeStreamModule)

    assert service is not None
    assert service.config.client_id == "stream-cid"
    assert service.config.client_secret == "stream-secret"
    assert service.config.confirmation_card_template_id == ""
