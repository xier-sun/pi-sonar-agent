"""Database client for storing run metadata and issue tracking."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import mysql.connector


@dataclass
class RunRecord:
    """Record of a fix run."""

    id: int
    run_timestamp: str
    author: str
    project_key: str
    repository: str
    total_issues: int
    successful_fixes: int
    failed_fixes: int
    status: str
    error: str
    pr_url: str


class MySQLClient:
    """MySQL database client for metadata storage."""

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        connection_timeout: int = 5,
    ):
        self.config = {
            "host": host,
            "port": port,
            "user": user,
            "password": password,
            "database": database,
            "connection_timeout": connection_timeout,
        }
        self._conn = None

    def connect(self) -> None:
        """Connect to the database."""
        try:
            self._conn = mysql.connector.connect(**self.config)
        except mysql.connector.Error as e:
            raise RuntimeError(f"Failed to connect to MySQL: {e}") from e

    def disconnect(self) -> None:
        """Disconnect from the database."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def ensure_tables(self) -> None:
        """Ensure required tables exist."""
        if not self._conn:
            self.connect()

        cursor = self._conn.cursor()

        # Create run_records table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS fix_run_records (
                id INT AUTO_INCREMENT PRIMARY KEY,
                run_timestamp DATETIME NOT NULL,
                author VARCHAR(255) NOT NULL,
                project_key VARCHAR(255) NOT NULL,
                repository VARCHAR(255) NOT NULL,
                total_issues INT DEFAULT 0,
                successful_fixes INT DEFAULT 0,
                failed_fixes INT DEFAULT 0,
                status VARCHAR(50) DEFAULT 'running',
                error TEXT,
                pr_url VARCHAR(500),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_author (author),
                INDEX idx_project (project_key),
                INDEX idx_timestamp (run_timestamp)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        # Create issue_records table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS fix_issue_records (
                id INT AUTO_INCREMENT PRIMARY KEY,
                run_id INT,
                issue_key VARCHAR(255) NOT NULL,
                rule_id VARCHAR(100) NOT NULL,
                file_path VARCHAR(500),
                line_number INT,
                fix_status VARCHAR(50) DEFAULT 'pending',
                fix_engine VARCHAR(50),
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (run_id) REFERENCES fix_run_records(id) ON DELETE CASCADE,
                INDEX idx_issue (issue_key),
                INDEX idx_run (run_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        self._conn.commit()
        cursor.close()

    def insert_run_record(
        self,
        author: str,
        project_key: str,
        repository: str,
        total_issues: int,
    ) -> int:
        """Insert a new run record and return its ID."""
        if not self._conn:
            self.connect()

        cursor = self._conn.cursor()
        cursor.execute(
            """
            INSERT INTO fix_run_records
            (run_timestamp, author, project_key, repository, total_issues, status)
            VALUES (%s, %s, %s, %s, %s, 'running')
            """,
            (datetime.now(), author, project_key, repository, total_issues),
        )
        self._conn.commit()
        run_id = cursor.lastrowid
        cursor.close()
        return run_id

    def update_run_record(
        self,
        run_id: int,
        successful_fixes: int = None,
        failed_fixes: int = None,
        status: str = None,
        error: str = None,
        pr_url: str = None,
    ) -> None:
        """Update a run record."""
        if not self._conn:
            self.connect()

        updates = []
        params = []

        if successful_fixes is not None:
            updates.append("successful_fixes = %s")
            params.append(successful_fixes)
        if failed_fixes is not None:
            updates.append("failed_fixes = %s")
            params.append(failed_fixes)
        if status is not None:
            updates.append("status = %s")
            params.append(status)
        if error is not None:
            updates.append("error = %s")
            params.append(error)
        if pr_url is not None:
            updates.append("pr_url = %s")
            params.append(pr_url)

        if not updates:
            return

        params.append(run_id)
        cursor = self._conn.cursor()
        cursor.execute(
            f"UPDATE fix_run_records SET {', '.join(updates)} WHERE id = %s",
            params,
        )
        self._conn.commit()
        cursor.close()

    def insert_issue_record(
        self,
        run_id: int,
        issue_key: str,
        rule_id: str,
        file_path: str,
        line_number: int,
    ) -> None:
        """Insert an issue record."""
        if not self._conn:
            self.connect()

        cursor = self._conn.cursor()
        cursor.execute(
            """
            INSERT INTO fix_issue_records
            (run_id, issue_key, rule_id, file_path, line_number, fix_status)
            VALUES (%s, %s, %s, %s, %s, 'pending')
            """,
            (run_id, issue_key, rule_id, file_path, line_number),
        )
        self._conn.commit()
        cursor.close()

    def update_issue_record(
        self,
        issue_key: str,
        fix_status: str,
        fix_engine: str = None,
        error_message: str = None,
    ) -> None:
        """Update an issue record."""
        if not self._conn:
            self.connect()

        updates = ["fix_status = %s"]
        params = [fix_status]

        if fix_engine:
            updates.append("fix_engine = %s")
            params.append(fix_engine)
        if error_message:
            updates.append("error_message = %s")
            params.append(error_message)

        params.append(issue_key)
        cursor = self._conn.cursor()
        cursor.execute(
            f"UPDATE fix_issue_records SET {', '.join(updates)} WHERE issue_key = %s",
            params,
        )
        self._conn.commit()
        cursor.close()

    def get_run_record(self, run_id: int) -> RunRecord | None:
        """Get a run record by ID."""
        if not self._conn:
            self.connect()

        cursor = self._conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM fix_run_records WHERE id = %s", (run_id,))
        row = cursor.fetchone()
        cursor.close()

        if not row:
            return None

        return RunRecord(
            id=row["id"],
            run_timestamp=str(row["run_timestamp"]),
            author=row["author"],
            project_key=row["project_key"],
            repository=row["repository"],
            total_issues=row["total_issues"],
            successful_fixes=row["successful_fixes"],
            failed_fixes=row["failed_fixes"],
            status=row["status"],
            error=row["error"] or "",
            pr_url=row["pr_url"] or "",
        )

    def lookup_dingtalk_userid_by_email(self, email: str) -> str | None:
        """Resolve DingTalk user ID from erp4.dingtalkuserdetail by email."""
        if not self._conn:
            self.connect()

        normalized_email = email.strip()
        if not normalized_email:
            return None

        cursor = self._conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT UserId
            FROM erp4.dingtalkuserdetail
            WHERE Email = %s
            LIMIT 1
            """,
            (normalized_email,),
        )
        row = cursor.fetchone()
        cursor.close()

        if not row:
            return None

        user_id = str(row.get("UserId", "")).strip()
        return user_id or None


def create_mysql_client_from_env() -> MySQLClient | None:
    """Create MySQL client from environment variables."""
    import os

    host = os.getenv("DB_HOST", "").strip()
    port = os.getenv("DB_PORT", "").strip()
    user = os.getenv("DB_USER", "").strip()
    password = os.getenv("DB_PASSWORD", "").strip()
    database = os.getenv("DB_NAME", "").strip()
    connection_timeout = os.getenv("DB_CONNECT_TIMEOUT", "").strip()

    if not all([host, user, password, database]):
        return None

    return MySQLClient(
        host=host,
        port=int(port) if port else 3306,
        user=user,
        password=password,
        database=database,
        connection_timeout=int(connection_timeout) if connection_timeout else 5,
    )
