from __future__ import annotations

from pi_sonar_agent.core.db_client import MySQLClient
from pi_sonar_agent.core.recipient_resolution import resolve_recipients


class _FakeCursor:
    def __init__(self, row):
        self.row = row
        self.executed: list[tuple[str, tuple[str, ...]]] = []
        self.closed = False

    def execute(self, query: str, params: tuple[str, ...]) -> None:
        self.executed.append((query, params))

    def fetchone(self):
        return self.row

    def close(self) -> None:
        self.closed = True


class _FakeConnection:
    def __init__(self, row):
        self.cursor_instance = _FakeCursor(row)

    def cursor(self, dictionary: bool = False):
        assert dictionary is True
        return self.cursor_instance


class _StubMySQLClient:
    def __init__(self, user_id: str | None) -> None:
        self.user_id = user_id
        self.lookups: list[str] = []

    def lookup_dingtalk_userid_by_email(self, email: str) -> str | None:
        self.lookups.append(email)
        return self.user_id


def test_lookup_dingtalk_userid_by_email_queries_expected_table() -> None:
    client = MySQLClient(
        host="127.0.0.1",
        port=3306,
        user="user",
        password="pwd",
        database="erp4",
    )
    fake_connection = _FakeConnection({"UserId": "17556530801301497"})
    client._conn = fake_connection

    user_id = client.lookup_dingtalk_userid_by_email("liyinglin@neware.com.cn")

    assert user_id == "17556530801301497"
    query, params = fake_connection.cursor_instance.executed[0]
    assert "FROM erp4.dingtalkuserdetail" in query
    assert "WHERE Email = %s" in query
    assert params == ("liyinglin@neware.com.cn",)
    assert fake_connection.cursor_instance.closed is True


def test_resolve_recipients_prefers_targets_values() -> None:
    mysql_client = _StubMySQLClient("db-user-id")

    recipients = resolve_recipients(
        author="liyinglin@neware.com.cn",
        configured_reviewer_email="pengxiru@neware.com.cn",
        configured_dingtalk_userid="17556530801301497",
        mysql_client=mysql_client,
    )

    assert recipients.reviewer_email == "pengxiru@neware.com.cn"
    assert recipients.reviewer_source == "targets.json.reviewer_email"
    assert recipients.dingtalk_userid == "17556530801301497"
    assert recipients.dingtalk_source == "targets.json.dingtalk_userid"
    assert mysql_client.lookups == []


def test_resolve_recipients_falls_back_to_author_and_mysql_lookup() -> None:
    mysql_client = _StubMySQLClient("resolved-from-db")

    recipients = resolve_recipients(
        author="liyinglin@neware.com.cn",
        configured_reviewer_email="",
        configured_dingtalk_userid="",
        mysql_client=mysql_client,
    )

    assert recipients.reviewer_email == "liyinglin@neware.com.cn"
    assert recipients.reviewer_source == "author"
    assert recipients.dingtalk_userid == "resolved-from-db"
    assert recipients.dingtalk_source == "mysql.author_email"
    assert mysql_client.lookups == ["liyinglin@neware.com.cn"]


def test_resolve_recipients_returns_unresolved_when_mysql_has_no_match() -> None:
    mysql_client = _StubMySQLClient(None)

    recipients = resolve_recipients(
        author="liyinglin@neware.com.cn",
        configured_reviewer_email="",
        configured_dingtalk_userid="",
        mysql_client=mysql_client,
    )

    assert recipients.reviewer_email == "liyinglin@neware.com.cn"
    assert recipients.dingtalk_userid is None
    assert recipients.dingtalk_source == "unresolved"
