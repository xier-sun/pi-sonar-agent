"""Resolution helpers for reviewer email and DingTalk recipients."""

from __future__ import annotations

from dataclasses import dataclass

from pi_sonar_agent.core.db_client import MySQLClient


@dataclass(frozen=True)
class ResolvedRecipients:
    """Resolved reviewer and DingTalk notification recipients."""

    reviewer_email: str
    reviewer_source: str
    dingtalk_userid: str | None
    dingtalk_source: str


def resolve_recipients(
    *,
    author: str,
    configured_reviewer_email: str = "",
    configured_dingtalk_userid: str = "",
    mysql_client: MySQLClient | None = None,
) -> ResolvedRecipients:
    """Resolve recipients with target-config priority and author fallback."""

    normalized_author = author.strip()
    reviewer_email = configured_reviewer_email.strip() or normalized_author
    reviewer_source = "targets.json.reviewer_email" if configured_reviewer_email.strip() else "author"

    if configured_dingtalk_userid.strip():
        return ResolvedRecipients(
            reviewer_email=reviewer_email,
            reviewer_source=reviewer_source,
            dingtalk_userid=configured_dingtalk_userid.strip(),
            dingtalk_source="targets.json.dingtalk_userid",
        )

    if mysql_client and normalized_author:
        user_id = mysql_client.lookup_dingtalk_userid_by_email(normalized_author)
        if user_id:
            return ResolvedRecipients(
                reviewer_email=reviewer_email,
                reviewer_source=reviewer_source,
                dingtalk_userid=user_id,
                dingtalk_source="mysql.author_email",
            )

    return ResolvedRecipients(
        reviewer_email=reviewer_email,
        reviewer_source=reviewer_source,
        dingtalk_userid=None,
        dingtalk_source="unresolved",
    )
