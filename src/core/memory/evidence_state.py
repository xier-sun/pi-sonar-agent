"""Evidence records attached to issue-level working memory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pi_sonar_agent.core.memory.memory_schema import (
    MemorySchemaError,
    ensure_string,
    ensure_tuple_of_strings,
    ensure_version,
)
from pi_sonar_agent.core.state import serialize_state


EVIDENCE_STATE_VERSION = 1
VALID_EVIDENCE_STATUS = {"current", "historical", "stale", "superseded"}


@dataclass(frozen=True)
class EvidenceState:
    """Structured evidence item used to invalidate stale failures safely."""

    version: int
    evidence_id: str
    source_type: str
    summary: str
    related_files: tuple[str, ...] = ()
    related_symbols: tuple[str, ...] = ()
    status: str = "current"
    content_fingerprint: str = ""
    diff_fingerprint: str = ""
    superseded_by: str = ""

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EvidenceState":
        if not isinstance(payload, dict):
            raise MemorySchemaError("EvidenceState payload must be an object.")
        status = ensure_string(payload.get("status"), field_name="status") or "current"
        if status not in VALID_EVIDENCE_STATUS:
            raise MemorySchemaError(f"status={status!r} is not supported.")
        return cls(
            version=ensure_version(
                payload.get("version"),
                expected=EVIDENCE_STATE_VERSION,
            ),
            evidence_id=ensure_string(
                payload.get("evidence_id"),
                field_name="evidence_id",
                required=True,
            ),
            source_type=ensure_string(
                payload.get("source_type"),
                field_name="source_type",
                required=True,
            ),
            summary=ensure_string(payload.get("summary"), field_name="summary", required=True),
            related_files=ensure_tuple_of_strings(
                payload.get("related_files"),
                field_name="related_files",
            ),
            related_symbols=ensure_tuple_of_strings(
                payload.get("related_symbols"),
                field_name="related_symbols",
            ),
            status=status,
            content_fingerprint=ensure_string(
                payload.get("content_fingerprint"),
                field_name="content_fingerprint",
            ),
            diff_fingerprint=ensure_string(
                payload.get("diff_fingerprint"),
                field_name="diff_fingerprint",
            ),
            superseded_by=ensure_string(
                payload.get("superseded_by"),
                field_name="superseded_by",
            ),
        )
