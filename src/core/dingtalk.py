"""DingTalk notification client."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Any

import requests


@dataclass
class DingTalkMessage:
    """DingTalk message."""

    title: str
    text: str
    message_type: str = "text"


class DingTalkCorpClient:
    """DingTalk Corporate API client."""

    def __init__(
        self,
        appkey: str,
        appsecret: str,
        agentid: str | None = None,
    ):
        self.appkey = appkey
        self.appsecret = appsecret
        self.agentid = agentid
        self._access_token: str | None = None
        self._token_expires_at: float = 0

    def get_access_token(self) -> str:
        """Get or refresh access token."""
        now = time.time()
        if self._access_token and now < self._token_expires_at:
            return self._access_token

        # Fetch new token
        url = "https://api.dingtalk.com/v1.0/robot/oAuth/token"
        params = {
            "appkey": self.appkey,
            "appsecret": self.appsecret,
        }

        response = requests.post(url, params=params, timeout=10)
        response.raise_for_status()

        data = response.json()
        self._access_token = data.get("accessToken", "")
        # Default expiry is 2 hours, reserve 5 minutes
        self._token_expires_at = now + data.get("expireIn", 7200) - 300

        return self._access_token

    def send_text_message(
        self,
        content: str,
        userid: str | None = None,
        webhook: str | None = None,
    ) -> dict[str, Any]:
        """Send a text message."""
        if webhook:
            return self._send_webhook(webhook, {"msgtype": "text", "text": {"content": content}})

        # Use robot API
        token = self.get_access_token()
        url = f"https://api.dingtalk.com/v1.0/robot/message/sendToConversation?accessToken={token}"

        body: dict[str, Any] = {
            "msgParam": '{"content":"' + content + '"}',
            "msgType": "text",
        }

        if self.agentid:
            body["agentId"] = self.agentid
        if userid:
            body["userId"] = userid

        response = requests.post(url, json=body, timeout=10)
        return response.json()

    def send_markdown_message(
        self,
        title: str,
        text: str,
        userid: str | None = None,
    ) -> dict[str, Any]:
        """Send a markdown message."""
        token = self.get_access_token()
        url = f"https://api.dingtalk.com/v1.0/robot/message/sendToConversation?accessToken={token}"

        body: dict[str, Any] = {
            "msgParam": '{"title":"' + title + '","text":"' + text + '"}',
            "msgType": "markdown",
        }

        if self.agentid:
            body["agentId"] = self.agentid
        if userid:
            body["userId"] = userid

        response = requests.post(url, json=body, timeout=10)
        return response.json()

    def send_link_message(
        self,
        title: str,
        text: str,
        message_url: str,
        pic_url: str | None = None,
    ) -> dict[str, Any]:
        """Send a link message."""
        token = self.get_access_token()
        url = f"https://api.dingtalk.com/v1.0/robot/message/sendToConversation?accessToken={token}"

        body: dict[str, Any] = {
            "msgParam": '{"title":"' + title + '","text":"' + text + '","messageUrl":"' + message_url + '"}',
            "msgType": "link",
        }

        if self.agentid:
            body["agentId"] = self.agentid

        response = requests.post(url, json=body, timeout=10)
        return response.json()

    def _send_webhook(self, webhook: str, body: dict[str, Any]) -> dict[str, Any]:
        """Send message via webhook."""
        response = requests.post(webhook, json=body, timeout=10)
        return response.json()

    def send_run_notification(
        self,
        author: str,
        total_issues: int,
        successful: int,
        failed: int,
        pr_url: str | None = None,
        dingtalk_userid: str | None = None,
    ) -> dict[str, Any]:
        """Send notification about fix run results."""
        status_emoji = "✅" if failed == 0 else "⚠️"
        title = f"{status_emoji} SonarQube 修复完成 - {author}"

        text = f"""## 修复报告

- **作者**: {author}
- **总问题数**: {total_issues}
- **成功修复**: {successful} ✅
- **修复失败**: {failed} ❌

"""

        if pr_url:
            text += f"- **PR 链接**: [查看 PR]({pr_url})\n"

        return self.send_markdown_message(title, text, userid=dingtalk_userid)


def create_dingtalk_client_from_env() -> DingTalkCorpClient | None:
    """Create DingTalk client from environment variables."""
    import os

    appkey = os.getenv("DINGTALK_APPKEY", "").strip()
    appsecret = os.getenv("DINGTALK_APPSECRET", "").strip()
    agentid = os.getenv("DINGTALK_AGENTID", "").strip()

    if not all([appkey, appsecret]):
        return None

    return DingTalkCorpClient(
        appkey=appkey,
        appsecret=appsecret,
        agentid=agentid or None,
    )