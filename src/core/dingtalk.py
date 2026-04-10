"""DingTalk notification client."""

from __future__ import annotations

import base64
import hmac
import time
import urllib.parse
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
    """DingTalk notification client with corp-message and webhook support."""

    def __init__(
        self,
        appkey: str | None = None,
        appsecret: str | None = None,
        agentid: str | None = None,
        webhook: str | None = None,
        webhook_secret: str | None = None,
    ) -> None:
        self.appkey = (appkey or "").strip()
        self.appsecret = (appsecret or "").strip()
        self.agentid = agentid
        self.webhook = (webhook or "").strip() or None
        self.webhook_secret = (webhook_secret or "").strip() or None
        self._access_token: str | None = None
        self._token_expires_at: float = 0

    def _has_corp_credentials(self) -> bool:
        return bool(self.appkey and self.appsecret)

    def _has_corp_message_config(self) -> bool:
        return self._has_corp_credentials() and bool(self.agentid)

    def _has_webhook_config(self) -> bool:
        return bool(self.webhook)

    def get_access_token(self) -> str:
        """Get or refresh access token."""
        now = time.time()
        if self._access_token and now < self._token_expires_at:
            return self._access_token

        if not self._has_corp_credentials():
            raise RuntimeError("缺少钉钉企业应用凭据，无法获取 access token")

        url = "https://oapi.dingtalk.com/gettoken"
        params = {
            "appkey": self.appkey,
            "appsecret": self.appsecret,
        }

        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()

        data = response.json()
        errcode = int(data.get("errcode", 0) or 0)
        if errcode != 0:
            raise RuntimeError(f"获取钉钉 access token 失败: {data.get('errmsg', 'unknown error')}")

        token = str(data.get("access_token", "")).strip()
        if not token:
            raise RuntimeError("获取钉钉 access token 失败: 响应中缺少 access_token")

        self._access_token = token
        # Default expiry is 2 hours, reserve 5 minutes
        self._token_expires_at = now + int(data.get("expires_in", 7200) or 7200) - 300

        return self._access_token

    def _send_corp_message(
        self,
        *,
        userid: str,
        msg: dict[str, Any],
    ) -> dict[str, Any]:
        """Send a private work notification via DingTalk internal app."""

        if not self._has_corp_message_config():
            raise RuntimeError("缺少钉钉企业应用配置，无法发送工作通知")
        if not userid:
            raise RuntimeError("缺少 dingtalk_userid，无法发送钉钉工作通知私信")

        token = self.get_access_token()
        response = requests.post(
            "https://oapi.dingtalk.com/topapi/message/corpconversation/asyncsend_v2",
            params={"access_token": token},
            json={
                "agent_id": int(self.agentid or 0),
                "userid_list": userid,
                "to_all_user": False,
                "msg": msg,
            },
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        errcode = int(data.get("errcode", 0) or 0)
        if errcode != 0:
            raise RuntimeError(f"发送钉钉工作通知失败: {data.get('errmsg', 'unknown error')}")
        return data

    def send_text_message(
        self,
        content: str,
        userid: str | None = None,
        webhook: str | None = None,
    ) -> dict[str, Any]:
        """Send a text message."""
        if userid and self._has_corp_message_config():
            return self._send_corp_message(
                userid=userid,
                msg={
                    "msgtype": "text",
                    "text": {"content": content},
                },
            )

        target_webhook = (webhook or self.webhook or "").strip()
        if target_webhook:
            return self._send_webhook(target_webhook, {"msgtype": "text", "text": {"content": content}})

        raise RuntimeError("缺少可用的钉钉通知配置")

    def send_markdown_message(
        self,
        title: str,
        text: str,
        userid: str | None = None,
        webhook: str | None = None,
    ) -> dict[str, Any]:
        """Send a markdown message."""
        if userid and self._has_corp_message_config():
            return self._send_corp_message(
                userid=userid,
                msg={
                    "msgtype": "markdown",
                    "markdown": {
                        "title": title,
                        "text": text,
                    },
                },
            )

        target_webhook = (webhook or self.webhook or "").strip()
        if target_webhook:
            return self._send_webhook(
                target_webhook,
                {
                    "msgtype": "markdown",
                    "markdown": {
                        "title": title,
                        "text": text,
                    },
                },
            )

        raise RuntimeError("缺少可用的钉钉通知配置")

    def send_link_message(
        self,
        title: str,
        text: str,
        message_url: str,
        pic_url: str | None = None,
    ) -> dict[str, Any]:
        """Send a link message."""
        if self._has_webhook_config():
            markdown = f"### {title}\n\n{text}\n\n[查看详情]({message_url})"
            return self._send_webhook(
                self.webhook or "",
                {
                    "msgtype": "markdown",
                    "markdown": {
                        "title": title,
                        "text": markdown,
                    },
                },
            )

        raise RuntimeError("当前仅支持通过 webhook 发送 link 风格通知")

    def _build_webhook_url(self, webhook: str) -> str:
        """Attach DingTalk signature when a robot secret is configured."""

        if not self.webhook_secret:
            return webhook

        timestamp = str(round(time.time() * 1000))
        string_to_sign = f"{timestamp}\n{self.webhook_secret}"
        digest = hmac.new(
            self.webhook_secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            digestmod="sha256",
        ).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(digest))
        separator = "&" if "?" in webhook else "?"
        return f"{webhook}{separator}timestamp={timestamp}&sign={sign}"

    def _send_webhook(self, webhook: str, body: dict[str, Any]) -> dict[str, Any]:
        """Send message via webhook."""
        response = requests.post(self._build_webhook_url(webhook), json=body, timeout=10)
        response.raise_for_status()
        data = response.json()
        errcode = int(data.get("errcode", 0) or 0)
        if errcode != 0:
            raise RuntimeError(f"发送钉钉 webhook 失败: {data.get('errmsg', 'unknown error')}")
        return data

    def send_run_notification(
        self,
        author: str,
        total_issues: int,
        successful: int,
        skipped: int,
        failed: int,
        pr_url: str | None = None,
        dingtalk_userid: str | None = None,
        warning_message: str | None = None,
        force_warn: bool = False,
    ) -> dict[str, Any]:
        """Send notification about fix run results."""
        status_tag = "[SUCCESS]" if failed == 0 and skipped == 0 and not force_warn else "[WARN]"
        title = f"{status_tag} SonarQube 修复完成 - {author}"

        text = f"""## 修复报告

- **作者**: {author}
- **总问题数**: {total_issues}
- **成功修复**: {successful} ✅
- **跳过修复**: {skipped} ⏭️
- **修复失败**: {failed} ❌

"""

        if pr_url:
            text += f"- **PR 链接**: [查看 PR]({pr_url})\n"
        if warning_message:
            text += f"- **附加说明**: {warning_message}\n"

        errors: list[str] = []

        if dingtalk_userid and self._has_corp_message_config():
            try:
                return self.send_markdown_message(title, text, userid=dingtalk_userid)
            except Exception as exc:
                errors.append(str(exc))

        if self._has_webhook_config():
            try:
                return self.send_markdown_message(title, text, webhook=self.webhook)
            except Exception as exc:
                errors.append(str(exc))

        if errors:
            raise RuntimeError(" ; ".join(errors))

        if self._has_corp_message_config() and not dingtalk_userid:
            raise RuntimeError("缺少 dingtalk_userid，且未配置 DINGTALK_WEBHOOK，无法发送通知")

        raise RuntimeError("未配置可用的钉钉通知渠道")


def create_dingtalk_client_from_env() -> DingTalkCorpClient | None:
    """Create DingTalk client from environment variables."""
    from pi_sonar_agent.core.project_env import read_project_env

    project_env = read_project_env()
    appkey = project_env.get("DINGTALK_APPKEY", "").strip()
    appsecret = project_env.get("DINGTALK_APPSECRET", "").strip()
    agentid = project_env.get("DINGTALK_AGENTID", "").strip()
    webhook = project_env.get("DINGTALK_WEBHOOK", "").strip()
    webhook_secret = project_env.get("DINGTALK_SECRET", "").strip()

    has_corp = bool(appkey and appsecret)
    has_webhook = bool(webhook)

    if not has_corp and not has_webhook:
        return None

    return DingTalkCorpClient(
        appkey=appkey if has_corp else None,
        appsecret=appsecret if has_corp else None,
        agentid=agentid or None if has_corp else None,
        webhook=webhook or None,
        webhook_secret=webhook_secret or None,
    )
