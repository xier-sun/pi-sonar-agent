"""Database client for storing run metadata and issue tracking."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

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

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS fix_state_snapshots (
                id INT AUTO_INCREMENT PRIMARY KEY,
                run_label VARCHAR(64) NOT NULL,
                entity_type VARCHAR(32) NOT NULL,
                entity_key VARCHAR(255) NOT NULL,
                repository VARCHAR(255),
                author VARCHAR(255),
                project_key VARCHAR(255),
                issue_key VARCHAR(255),
                attempt_number INT DEFAULT 0,
                status VARCHAR(50) DEFAULT '',
                artifact_path VARCHAR(1000),
                payload_json LONGTEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uniq_state_snapshot (run_label, entity_type, entity_key, attempt_number),
                INDEX idx_snapshot_run (run_label),
                INDEX idx_snapshot_issue (issue_key),
                INDEX idx_snapshot_entity (entity_type, entity_key)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS fix_event_records (
                id INT AUTO_INCREMENT PRIMARY KEY,
                run_label VARCHAR(64) NOT NULL,
                event_kind VARCHAR(32) NOT NULL,
                entity_type VARCHAR(32) NOT NULL,
                entity_key VARCHAR(255) NOT NULL,
                repository VARCHAR(255),
                author VARCHAR(255),
                project_key VARCHAR(255),
                issue_key VARCHAR(255),
                attempt_number INT DEFAULT 0,
                status VARCHAR(50) DEFAULT '',
                artifact_path VARCHAR(1000),
                payload_json LONGTEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_event_run (run_label),
                INDEX idx_event_issue (issue_key),
                INDEX idx_event_kind (event_kind)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS fix_pull_request_records (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                run_label VARCHAR(64) NOT NULL,
                project_key VARCHAR(255) NOT NULL,
                repository VARCHAR(255) NOT NULL,
                author VARCHAR(255) NOT NULL,
                base_branch VARCHAR(255) NOT NULL,
                source_branch VARCHAR(255) NOT NULL,
                pr_provider VARCHAR(32) NOT NULL DEFAULT 'azure_devops',
                pr_id BIGINT NULL,
                pr_url VARCHAR(1000) NULL,
                pr_title VARCHAR(500) NULL,
                pr_status VARCHAR(64) NOT NULL DEFAULT 'created',
                pr_description_path VARCHAR(1000) NULL,
                pr_attachment_name VARCHAR(255) NULL,
                pr_attachment_url VARCHAR(1000) NULL,
                target_status VARCHAR(32) NOT NULL,
                partial_pr TINYINT(1) NOT NULL DEFAULT 0,
                build_passed_before_pr TINYINT(1) NOT NULL DEFAULT 0,
                total_issues INT NOT NULL DEFAULT 0,
                successful_issues INT NOT NULL DEFAULT 0,
                skipped_issues INT NOT NULL DEFAULT 0,
                failed_issues INT NOT NULL DEFAULT 0,
                policy_skipped_issues INT NOT NULL DEFAULT 0,
                first_pass_issue_count INT NOT NULL DEFAULT 0,
                second_pass_issue_count INT NOT NULL DEFAULT 0,
                used_second_pass TINYINT(1) NOT NULL DEFAULT 0,
                used_abort_publish TINYINT(1) NOT NULL DEFAULT 0,
                effective_fix_rate DECIMAL(8,4) NULL,
                tier1_model VARCHAR(255) NULL,
                tier2_model VARCHAR(255) NULL,
                auto_complete_enabled TINYINT(1) NOT NULL DEFAULT 0,
                auto_complete_set_succeeded TINYINT(1) NOT NULL DEFAULT 0,
                delete_source_branch_on_complete TINYINT(1) NOT NULL DEFAULT 0,
                summary_artifact_path VARCHAR(1000) NULL,
                error_message TEXT NULL,
                started_at DATETIME NULL,
                finished_at DATETIME NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uniq_pr_run_author_repo (run_label, repository, author),
                KEY idx_pr_project_repo (project_key, repository),
                KEY idx_pr_author (author),
                KEY idx_pr_status (pr_status),
                KEY idx_pr_target_status (target_status),
                KEY idx_pr_started_at (started_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS fix_pull_request_issue_records (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                pr_record_id BIGINT NOT NULL,
                run_label VARCHAR(64) NOT NULL,
                project_key VARCHAR(255) NOT NULL,
                repository VARCHAR(255) NOT NULL,
                author VARCHAR(255) NOT NULL,
                issue_key VARCHAR(255) NOT NULL,
                rule_id VARCHAR(128) NOT NULL,
                severity VARCHAR(64) NULL,
                issue_type VARCHAR(64) NULL,
                file_path VARCHAR(1000) NULL,
                line_number INT NULL,
                message TEXT NULL,
                final_status VARCHAR(32) NOT NULL,
                included_in_pr TINYINT(1) NOT NULL DEFAULT 0,
                attempt_count INT NOT NULL DEFAULT 0,
                final_failure_kind VARCHAR(128) NULL,
                final_error TEXT NULL,
                final_skip_reason TEXT NULL,
                final_summary TEXT NULL,
                first_pass_result VARCHAR(32) NULL,
                second_pass_result VARCHAR(32) NULL,
                resolved_in_second_pass TINYINT(1) NOT NULL DEFAULT 0,
                tier_used_for_final_result VARCHAR(32) NULL,
                build_passed TINYINT(1) NOT NULL DEFAULT 0,
                post_fix_issue_status VARCHAR(64) NULL,
                boundary_failure_code VARCHAR(128) NULL,
                secondary_boundary_failure_codes TEXT NULL,
                quality_gate_status VARCHAR(64) NULL,
                hard_quality_gate_failures INT NOT NULL DEFAULT 0,
                soft_quality_gate_findings INT NOT NULL DEFAULT 0,
                boundary_drift_score INT NOT NULL DEFAULT 0,
                rule_review_summary_json LONGTEXT NULL,
                changed_files_json LONGTEXT NULL,
                issue_artifact_root VARCHAR(1000) NULL,
                issue_log_path VARCHAR(1000) NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                CONSTRAINT fk_pr_issue_pr_record
                    FOREIGN KEY (pr_record_id) REFERENCES fix_pull_request_records(id)
                    ON DELETE CASCADE,
                UNIQUE KEY uniq_pr_issue (pr_record_id, issue_key),
                KEY idx_pr_issue_issue_key (issue_key),
                KEY idx_pr_issue_rule (rule_id),
                KEY idx_pr_issue_status (final_status),
                KEY idx_pr_issue_repo_author (repository, author)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        try:
            cursor.execute(
                """
                ALTER TABLE fix_pull_request_issue_records
                ADD COLUMN IF NOT EXISTS rule_review_summary_json LONGTEXT NULL
                """
            )
        except mysql.connector.Error:
            pass

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS fix_pull_request_attempt_records (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                pr_record_id BIGINT NOT NULL,
                run_label VARCHAR(64) NOT NULL,
                issue_key VARCHAR(255) NOT NULL,
                attempt_number INT NOT NULL,
                pass_name VARCHAR(32) NOT NULL,
                tier_name VARCHAR(32) NULL,
                model_name VARCHAR(255) NULL,
                attempt_status VARCHAR(32) NOT NULL,
                failure_kind VARCHAR(128) NULL,
                retry_reason VARCHAR(128) NULL,
                retryable_failure TINYINT(1) NOT NULL DEFAULT 0,
                build_passed TINYINT(1) NOT NULL DEFAULT 0,
                build_verification_failed TINYINT(1) NOT NULL DEFAULT 0,
                model_timeout_stage VARCHAR(128) NULL,
                patch_salvaged TINYINT(1) NOT NULL DEFAULT 0,
                fast_path_enabled TINYINT(1) NOT NULL DEFAULT 0,
                execution_profile VARCHAR(64) NULL,
                guardrail_mode VARCHAR(64) NULL,
                execution_mode VARCHAR(64) NULL,
                changed_files_json LONGTEXT NULL,
                performance_metrics_json LONGTEXT NULL,
                summary TEXT NULL,
                error TEXT NULL,
                skip_reason TEXT NULL,
                artifact_dir VARCHAR(1000) NULL,
                attempt_events_path VARCHAR(1000) NULL,
                started_at DATETIME NULL,
                finished_at DATETIME NULL,
                duration_seconds DECIMAL(12,3) NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                CONSTRAINT fk_pr_attempt_pr_record
                    FOREIGN KEY (pr_record_id) REFERENCES fix_pull_request_records(id)
                    ON DELETE CASCADE,
                UNIQUE KEY uniq_pr_attempt (pr_record_id, issue_key, attempt_number),
                KEY idx_pr_attempt_issue (issue_key),
                KEY idx_pr_attempt_pass (pass_name),
                KEY idx_pr_attempt_tier (tier_name),
                KEY idx_pr_attempt_status (attempt_status),
                KEY idx_pr_attempt_failure_kind (failure_kind)
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

    def upsert_state_snapshot(
        self,
        *,
        run_label: str,
        entity_type: str,
        entity_key: str,
        payload: dict[str, Any],
        repository: str = "",
        author: str = "",
        project_key: str = "",
        issue_key: str = "",
        attempt_number: int = 0,
        status: str = "",
        artifact_path: str = "",
    ) -> None:
        """Insert or update a structured state snapshot."""

        if not self._conn:
            self.connect()

        cursor = self._conn.cursor()
        payload_json = json.dumps(payload, ensure_ascii=False)
        cursor.execute(
            """
            INSERT INTO fix_state_snapshots
            (
                run_label,
                entity_type,
                entity_key,
                repository,
                author,
                project_key,
                issue_key,
                attempt_number,
                status,
                artifact_path,
                payload_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                repository = VALUES(repository),
                author = VALUES(author),
                project_key = VALUES(project_key),
                issue_key = VALUES(issue_key),
                status = VALUES(status),
                artifact_path = VALUES(artifact_path),
                payload_json = VALUES(payload_json)
            """,
            (
                run_label,
                entity_type,
                entity_key,
                repository,
                author,
                project_key,
                issue_key,
                attempt_number,
                status,
                artifact_path,
                payload_json,
            ),
        )
        self._conn.commit()
        cursor.close()

    def insert_event_record(
        self,
        *,
        run_label: str,
        event_kind: str,
        entity_type: str,
        entity_key: str,
        payload: dict[str, Any],
        repository: str = "",
        author: str = "",
        project_key: str = "",
        issue_key: str = "",
        attempt_number: int = 0,
        status: str = "",
        artifact_path: str = "",
    ) -> None:
        """Insert a structured lifecycle event."""

        if not self._conn:
            self.connect()

        cursor = self._conn.cursor()
        cursor.execute(
            """
            INSERT INTO fix_event_records
            (
                run_label,
                event_kind,
                entity_type,
                entity_key,
                repository,
                author,
                project_key,
                issue_key,
                attempt_number,
                status,
                artifact_path,
                payload_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                run_label,
                event_kind,
                entity_type,
                entity_key,
                repository,
                author,
                project_key,
                issue_key,
                attempt_number,
                status,
                artifact_path,
                json.dumps(payload, ensure_ascii=False),
            ),
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

    def insert_pull_request_record(
        self,
        *,
        run_label: str,
        project_key: str,
        repository: str,
        author: str,
        base_branch: str,
        source_branch: str,
        pr_id: int | None,
        pr_url: str,
        pr_title: str,
        pr_status: str,
        pr_description_path: str = "",
        pr_attachment_name: str = "",
        pr_attachment_url: str = "",
        target_status: str = "",
        partial_pr: bool = False,
        build_passed_before_pr: bool = False,
        total_issues: int = 0,
        successful_issues: int = 0,
        skipped_issues: int = 0,
        failed_issues: int = 0,
        policy_skipped_issues: int = 0,
        first_pass_issue_count: int = 0,
        second_pass_issue_count: int = 0,
        used_second_pass: bool = False,
        used_abort_publish: bool = False,
        effective_fix_rate: float | None = None,
        tier1_model: str = "",
        tier2_model: str = "",
        auto_complete_enabled: bool = False,
        auto_complete_set_succeeded: bool = False,
        delete_source_branch_on_complete: bool = False,
        summary_artifact_path: str = "",
        error_message: str = "",
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> int:
        """Insert one PR-level business record and return its ID."""

        if not self._conn:
            self.connect()

        cursor = self._conn.cursor()
        cursor.execute(
            """
            INSERT INTO fix_pull_request_records
            (
                run_label, project_key, repository, author, base_branch, source_branch,
                pr_id, pr_url, pr_title, pr_status, pr_description_path,
                pr_attachment_name, pr_attachment_url, target_status, partial_pr,
                build_passed_before_pr, total_issues, successful_issues, skipped_issues,
                failed_issues, policy_skipped_issues, first_pass_issue_count,
                second_pass_issue_count, used_second_pass, used_abort_publish,
                effective_fix_rate, tier1_model, tier2_model, auto_complete_enabled,
                auto_complete_set_succeeded, delete_source_branch_on_complete,
                summary_artifact_path, error_message, started_at, finished_at
            )
            VALUES
            (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s,
                %s, %s, %s, %s
            )
            """,
            (
                run_label,
                project_key,
                repository,
                author,
                base_branch,
                source_branch,
                pr_id,
                pr_url or None,
                pr_title,
                pr_status,
                pr_description_path or None,
                pr_attachment_name or None,
                pr_attachment_url or None,
                target_status,
                int(bool(partial_pr)),
                int(bool(build_passed_before_pr)),
                total_issues,
                successful_issues,
                skipped_issues,
                failed_issues,
                policy_skipped_issues,
                first_pass_issue_count,
                second_pass_issue_count,
                int(bool(used_second_pass)),
                int(bool(used_abort_publish)),
                effective_fix_rate,
                tier1_model or None,
                tier2_model or None,
                int(bool(auto_complete_enabled)),
                int(bool(auto_complete_set_succeeded)),
                int(bool(delete_source_branch_on_complete)),
                summary_artifact_path or None,
                error_message or None,
                started_at,
                finished_at,
            ),
        )
        self._conn.commit()
        record_id = cursor.lastrowid
        cursor.close()
        return int(record_id)

    def insert_pull_request_issue_records(
        self,
        pr_record_id: int,
        rows: list[dict[str, Any]],
    ) -> None:
        """Bulk-insert PR issue result rows."""

        if not rows:
            return
        if not self._conn:
            self.connect()
        cursor = self._conn.cursor()
        payload = [
            (
                pr_record_id,
                row.get("run_label", ""),
                row.get("project_key", ""),
                row.get("repository", ""),
                row.get("author", ""),
                row.get("issue_key", ""),
                row.get("rule_id", ""),
                row.get("severity"),
                row.get("issue_type"),
                row.get("file_path"),
                row.get("line_number"),
                row.get("message"),
                row.get("final_status", ""),
                int(bool(row.get("included_in_pr", False))),
                int(row.get("attempt_count", 0) or 0),
                row.get("final_failure_kind"),
                row.get("final_error"),
                row.get("final_skip_reason"),
                row.get("final_summary"),
                row.get("first_pass_result"),
                row.get("second_pass_result"),
                int(bool(row.get("resolved_in_second_pass", False))),
                row.get("tier_used_for_final_result"),
                int(bool(row.get("build_passed", False))),
                row.get("post_fix_issue_status"),
                row.get("boundary_failure_code"),
                row.get("secondary_boundary_failure_codes"),
                row.get("quality_gate_status"),
                int(row.get("hard_quality_gate_failures", 0) or 0),
                int(row.get("soft_quality_gate_findings", 0) or 0),
                int(row.get("boundary_drift_score", 0) or 0),
                row.get("rule_review_summary_json"),
                row.get("changed_files_json"),
                row.get("issue_artifact_root"),
                row.get("issue_log_path"),
            )
            for row in rows
        ]
        cursor.executemany(
            """
            INSERT INTO fix_pull_request_issue_records
            (
                pr_record_id, run_label, project_key, repository, author, issue_key,
                rule_id, severity, issue_type, file_path, line_number, message,
                final_status, included_in_pr, attempt_count, final_failure_kind,
                final_error, final_skip_reason, final_summary, first_pass_result,
                second_pass_result, resolved_in_second_pass, tier_used_for_final_result,
                build_passed, post_fix_issue_status, boundary_failure_code,
                secondary_boundary_failure_codes, quality_gate_status,
                hard_quality_gate_failures, soft_quality_gate_findings,
                boundary_drift_score, rule_review_summary_json, changed_files_json,
                issue_artifact_root, issue_log_path
            )
            VALUES
            (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s,
                %s, %s,
                %s, %s, %s, %s, %s
            )
            """,
            payload,
        )
        self._conn.commit()
        cursor.close()

    def insert_pull_request_attempt_records(
        self,
        pr_record_id: int,
        rows: list[dict[str, Any]],
    ) -> None:
        """Bulk-insert PR attempt detail rows."""

        if not rows:
            return
        if not self._conn:
            self.connect()
        cursor = self._conn.cursor()
        payload = [
            (
                pr_record_id,
                row.get("run_label", ""),
                row.get("issue_key", ""),
                int(row.get("attempt_number", 0) or 0),
                row.get("pass_name", ""),
                row.get("tier_name"),
                row.get("model_name"),
                row.get("attempt_status", ""),
                row.get("failure_kind"),
                row.get("retry_reason"),
                int(bool(row.get("retryable_failure", False))),
                int(bool(row.get("build_passed", False))),
                int(bool(row.get("build_verification_failed", False))),
                row.get("model_timeout_stage"),
                int(bool(row.get("patch_salvaged", False))),
                int(bool(row.get("fast_path_enabled", False))),
                row.get("execution_profile"),
                row.get("guardrail_mode"),
                row.get("execution_mode"),
                row.get("changed_files_json"),
                row.get("performance_metrics_json"),
                row.get("summary"),
                row.get("error"),
                row.get("skip_reason"),
                row.get("artifact_dir"),
                row.get("attempt_events_path"),
                row.get("started_at"),
                row.get("finished_at"),
                row.get("duration_seconds"),
            )
            for row in rows
        ]
        cursor.executemany(
            """
            INSERT INTO fix_pull_request_attempt_records
            (
                pr_record_id, run_label, issue_key, attempt_number, pass_name,
                tier_name, model_name, attempt_status, failure_kind, retry_reason,
                retryable_failure, build_passed, build_verification_failed,
                model_timeout_stage, patch_salvaged, fast_path_enabled,
                execution_profile, guardrail_mode, execution_mode,
                changed_files_json, performance_metrics_json, summary, error,
                skip_reason, artifact_dir, attempt_events_path, started_at,
                finished_at, duration_seconds
            )
            VALUES
            (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s
            )
            """,
            payload,
        )
        self._conn.commit()
        cursor.close()


def create_mysql_client_from_env() -> MySQLClient | None:
    """Create MySQL client from environment variables."""
    from pi_sonar_agent.core.project_env import read_project_env

    project_env = read_project_env()
    host = project_env.get("DB_HOST", "").strip()
    port = project_env.get("DB_PORT", "").strip()
    user = project_env.get("DB_USER", "").strip()
    password = project_env.get("DB_PASSWORD", "").strip()
    database = project_env.get("DB_NAME", "").strip()
    connection_timeout = project_env.get("DB_CONNECT_TIMEOUT", "").strip()

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
