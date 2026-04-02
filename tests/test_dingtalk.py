from __future__ import annotations

from typing import Any

import pi_sonar_agent.core.dingtalk as dingtalk_module
from pi_sonar_agent.core.dingtalk import DingTalkCorpClient, create_dingtalk_client_from_env


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict[str, Any]:
        return self._payload


def test_send_run_notification_prefers_corp_private_message(monkeypatch) -> None:
    calls: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None]] = []

    def fake_get(url: str, params: dict[str, Any] | None = None, timeout: int = 0):
        calls.append(("GET", url, params, None))
        return _FakeResponse({"errcode": 0, "errmsg": "ok", "access_token": "token", "expires_in": 7200})

    def fake_post(
        url: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        timeout: int = 0,
    ):
        calls.append(("POST", url, params, json))
        return _FakeResponse({"errcode": 0, "errmsg": "ok", "task_id": 1})

    monkeypatch.setattr(dingtalk_module.requests, "get", fake_get)
    monkeypatch.setattr(dingtalk_module.requests, "post", fake_post)

    client = DingTalkCorpClient(appkey="ak", appsecret="sk", agentid="123", webhook="https://example.invalid")

    result = client.send_run_notification(
        author="liyinglin@neware.com.cn",
        total_issues=3,
        successful=2,
        failed=1,
        pr_url="https://example.com/pr/1",
        dingtalk_userid="17556530801301497",
    )

    assert result["task_id"] == 1
    assert calls[0][1] == "https://oapi.dingtalk.com/gettoken"
    assert calls[1][1] == "https://oapi.dingtalk.com/topapi/message/corpconversation/asyncsend_v2"
    assert calls[1][3]["userid_list"] == "17556530801301497"
    assert calls[1][3]["msg"]["msgtype"] == "markdown"


def test_send_run_notification_falls_back_to_signed_webhook(monkeypatch) -> None:
    calls: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None]] = []

    def fake_get(url: str, params: dict[str, Any] | None = None, timeout: int = 0):
        calls.append(("GET", url, params, None))
        return _FakeResponse({"errcode": 0, "errmsg": "ok", "access_token": "token", "expires_in": 7200})

    def fake_post(
        url: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        timeout: int = 0,
    ):
        calls.append(("POST", url, params, json))
        if "corpconversation" in url:
            return _FakeResponse({"errcode": 60011, "errmsg": "userid not found"})
        return _FakeResponse({"errcode": 0, "errmsg": "ok"})

    monkeypatch.setattr(dingtalk_module.requests, "get", fake_get)
    monkeypatch.setattr(dingtalk_module.requests, "post", fake_post)
    monkeypatch.setattr(dingtalk_module.time, "time", lambda: 1.0)

    client = DingTalkCorpClient(
        appkey="ak",
        appsecret="sk",
        agentid="123",
        webhook="https://oapi.dingtalk.com/robot/send?access_token=test",
        webhook_secret="SEC-test",
    )

    result = client.send_run_notification(
        author="liyinglin@neware.com.cn",
        total_issues=1,
        successful=1,
        failed=0,
        pr_url="https://example.com/pr/2",
        dingtalk_userid="missing-user",
    )

    assert result["errmsg"] == "ok"
    assert calls[1][1] == "https://oapi.dingtalk.com/topapi/message/corpconversation/asyncsend_v2"
    assert "timestamp=1000" in calls[2][1]
    assert "sign=" in calls[2][1]
    assert calls[2][3]["msgtype"] == "markdown"


def test_send_run_notification_includes_warning_message(monkeypatch) -> None:
    calls: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None]] = []

    def fake_post(
        url: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        timeout: int = 0,
    ):
        calls.append(("POST", url, params, json))
        return _FakeResponse({"errcode": 0, "errmsg": "ok"})

    monkeypatch.setattr(dingtalk_module.requests, "post", fake_post)

    client = DingTalkCorpClient(webhook="https://oapi.dingtalk.com/robot/send?access_token=test")

    result = client.send_run_notification(
        author="pengxiru@neware.com.cn",
        total_issues=10,
        successful=9,
        failed=1,
        pr_url=None,
        warning_message="PR 创建失败：HTTP 400",
        force_warn=True,
    )

    assert result["errmsg"] == "ok"
    assert calls[0][3]["markdown"]["title"].startswith("[WARN]")
    assert "PR 创建失败：HTTP 400" in calls[0][3]["markdown"]["text"]


def test_create_dingtalk_client_from_env_supports_webhook_only(monkeypatch) -> None:
    monkeypatch.delenv("DINGTALK_APPKEY", raising=False)
    monkeypatch.delenv("DINGTALK_APPSECRET", raising=False)
    monkeypatch.delenv("DINGTALK_AGENTID", raising=False)
    monkeypatch.setenv("DINGTALK_WEBHOOK", "https://oapi.dingtalk.com/robot/send?access_token=test")
    monkeypatch.setenv("DINGTALK_SECRET", "SEC-test")

    client = create_dingtalk_client_from_env()

    assert client is not None
    assert client.webhook == "https://oapi.dingtalk.com/robot/send?access_token=test"
    assert client.webhook_secret == "SEC-test"
