"""Structured lessons memory for repeated retry, boundary, and quality-gate failures."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pi_sonar_agent.core.retry_context import RetryContext
from pi_sonar_agent.core.state import serialize_state, utc_now_iso


@dataclass(frozen=True)
class LessonRecord:
    """One structured failure lesson captured from a completed attempt."""

    recorded_at: str
    lesson_kind: str
    repository: str
    run_label: str
    issue_key: str
    source_attempt_number: int
    issue_rule_id: str
    failure_kind: str
    primary_failure_fingerprint: str = ""
    failure_fingerprints: tuple[str, ...] = ()
    scope_mode: str = ""
    guardrail_mode: str = ""
    boundary_failure_code: str = ""
    summary: str = ""
    guidance: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    quality_gate_rule_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


@dataclass(frozen=True)
class LessonPattern:
    """Aggregated recurring failure pattern derived from lesson records."""

    pattern_key: str
    lesson_kind: str
    issue_rule_id: str
    failure_kind: str
    primary_failure_fingerprint: str = ""
    failure_fingerprints: tuple[str, ...] = ()
    scope_mode: str = ""
    guardrail_mode: str = ""
    boundary_failure_code: str = ""
    count: int = 0
    first_seen_at: str = ""
    last_seen_at: str = ""
    latest_summary: str = ""
    guidance: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    quality_gate_rule_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


@dataclass(frozen=True)
class PlannerLesson:
    """Planner-facing lesson used to avoid repeated failures."""

    source: str
    summary: str
    guidance: tuple[str, ...] = ()
    issue_rule_id: str = ""
    failure_kind: str = ""
    primary_failure_fingerprint: str = ""
    failure_fingerprints: tuple[str, ...] = ()
    scope_mode: str = ""
    guardrail_mode: str = ""
    boundary_failure_code: str = ""
    quality_gate_rule_ids: tuple[str, ...] = ()
    selection_mode: str = ""
    selection_reason: str = ""
    count: int = 1

    def to_dict(self) -> dict[str, Any]:
        return serialize_state(self)


class LessonsStore:
    """Persist and retrieve structured lessons for repeated failure patterns."""

    def __init__(self, root: str | Path = "logs/lessons") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.quality_gate_lessons_path = self.root / "quality_gate_lessons.jsonl"
        self.boundary_patterns_path = self.root / "boundary_failure_patterns.json"
        self.rule_patterns_path = self.root / "rule_failure_patterns.json"

    @staticmethod
    def _dedupe(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
        seen: list[str] = []
        for value in values:
            text = str(value or "").strip()
            if not text or text in seen:
                continue
            seen.append(text)
        return tuple(seen)

    @classmethod
    def _pattern_key(cls, record: LessonRecord) -> str:
        return "|".join(
            [
                record.lesson_kind,
                record.issue_rule_id,
                record.failure_kind,
                record.primary_failure_fingerprint or "-",
                record.scope_mode or "-",
                record.guardrail_mode or "-",
                record.boundary_failure_code or "-",
            ]
        )

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _load_patterns(self, path: Path) -> dict[str, LessonPattern]:
        payload = self._load_json(path)
        patterns: dict[str, LessonPattern] = {}
        for item in payload.get("patterns", []):
            if not isinstance(item, dict):
                continue
            pattern_key = str(item.get("pattern_key", "")).strip()
            if not pattern_key:
                continue
            patterns[pattern_key] = LessonPattern(
                pattern_key=pattern_key,
                lesson_kind=str(item.get("lesson_kind", "")).strip(),
                issue_rule_id=str(item.get("issue_rule_id", "")).strip(),
                failure_kind=str(item.get("failure_kind", "")).strip(),
                primary_failure_fingerprint=str(item.get("primary_failure_fingerprint", "")).strip(),
                failure_fingerprints=self._dedupe(tuple(item.get("failure_fingerprints", ()))),
                scope_mode=str(item.get("scope_mode", "")).strip(),
                guardrail_mode=str(item.get("guardrail_mode", "")).strip(),
                boundary_failure_code=str(item.get("boundary_failure_code", "")).strip(),
                count=int(item.get("count", 0) or 0),
                first_seen_at=str(item.get("first_seen_at", "")).strip(),
                last_seen_at=str(item.get("last_seen_at", "")).strip(),
                latest_summary=str(item.get("latest_summary", "")).strip(),
                guidance=self._dedupe(tuple(item.get("guidance", ()))),
                evidence=self._dedupe(tuple(item.get("evidence", ()))),
                quality_gate_rule_ids=self._dedupe(tuple(item.get("quality_gate_rule_ids", ()))),
            )
        return patterns

    def _save_patterns(self, path: Path, patterns: dict[str, LessonPattern]) -> None:
        sorted_patterns = sorted(
            patterns.values(),
            key=lambda item: (-item.count, item.last_seen_at, item.pattern_key),
        )
        self._write_json(
            path,
            {
                "generated_at": utc_now_iso(),
                "patterns": [pattern.to_dict() for pattern in sorted_patterns],
            },
        )

    @staticmethod
    def _build_rule_failure_summary(retry_context: RetryContext) -> str:
        if retry_context.summary:
            return retry_context.summary
        if retry_context.error:
            return retry_context.error
        if retry_context.failure_kind:
            return f"Attempt failed with {retry_context.failure_kind}."
        return "Attempt failed and produced a retry context."

    def _build_records(
        self,
        *,
        repository: str,
        run_label: str,
        issue_key: str,
        issue_rule_id: str,
        retry_context: RetryContext,
        scope_mode: str,
        guardrail_mode: str,
        quality_gate_rule_ids: tuple[str, ...],
    ) -> tuple[LessonRecord, ...]:
        recorded_at = utc_now_iso()
        records: list[LessonRecord] = [
            LessonRecord(
                recorded_at=recorded_at,
                lesson_kind="rule_failure",
                repository=repository,
                run_label=run_label,
                issue_key=issue_key,
                source_attempt_number=retry_context.source_attempt_number,
                issue_rule_id=issue_rule_id,
                failure_kind=retry_context.failure_kind,
                primary_failure_fingerprint=str(
                    getattr(retry_context, "primary_failure_fingerprint", "") or ""
                ).strip(),
                failure_fingerprints=self._dedupe(getattr(retry_context, "failure_fingerprints", ()) or ()),
                scope_mode=scope_mode,
                guardrail_mode=guardrail_mode,
                boundary_failure_code=(
                    retry_context.boundary_failure.code
                    if retry_context.boundary_failure is not None
                    else ""
                ),
                summary=self._build_rule_failure_summary(retry_context),
                guidance=self._dedupe(retry_context.guidance),
                evidence=self._dedupe((retry_context.raw_output,)),
                quality_gate_rule_ids=self._dedupe(quality_gate_rule_ids),
            )
        ]

        if retry_context.scope_violation is not None:
            scope_violation = retry_context.scope_violation
            records.append(
                LessonRecord(
                    recorded_at=recorded_at,
                    lesson_kind="boundary",
                    repository=repository,
                    run_label=run_label,
                    issue_key=issue_key,
                    source_attempt_number=retry_context.source_attempt_number,
                    issue_rule_id=issue_rule_id,
                    failure_kind=retry_context.failure_kind or "scope",
                    primary_failure_fingerprint=str(
                        getattr(retry_context, "primary_failure_fingerprint", "") or ""
                    ).strip(),
                    failure_fingerprints=self._dedupe(getattr(retry_context, "failure_fingerprints", ()) or ()),
                    scope_mode=scope_mode,
                    guardrail_mode=guardrail_mode,
                    boundary_failure_code=(
                        retry_context.boundary_failure.code
                        if retry_context.boundary_failure is not None
                        else ""
                    ),
                    summary=scope_violation.raw_output or "Scope validation rejected the patch.",
                    guidance=self._dedupe(scope_violation.constraints),
                    evidence=self._dedupe(
                        (
                            scope_violation.allowed_lines,
                            scope_violation.changed_lines_outside_scope,
                        )
                    ),
                    quality_gate_rule_ids=self._dedupe(quality_gate_rule_ids),
                )
            )

        if retry_context.review_failure is not None:
            review_failure = retry_context.review_failure
            records.append(
                LessonRecord(
                    recorded_at=recorded_at,
                    lesson_kind="boundary",
                    repository=repository,
                    run_label=run_label,
                    issue_key=issue_key,
                    source_attempt_number=retry_context.source_attempt_number,
                    issue_rule_id=issue_rule_id,
                    failure_kind=retry_context.failure_kind or "reviewer",
                    primary_failure_fingerprint=str(
                        getattr(retry_context, "primary_failure_fingerprint", "") or ""
                    ).strip(),
                    failure_fingerprints=self._dedupe(getattr(retry_context, "failure_fingerprints", ()) or ()),
                    scope_mode=scope_mode,
                    guardrail_mode=guardrail_mode,
                    boundary_failure_code=(
                        retry_context.boundary_failure.code
                        if retry_context.boundary_failure is not None
                        else ""
                    ),
                    summary=review_failure.summary or "Diff reviewer rejected the patch.",
                    guidance=self._dedupe(review_failure.constraints),
                    evidence=self._dedupe(tuple(item.reason for item in review_failure.violations)),
                    quality_gate_rule_ids=self._dedupe(quality_gate_rule_ids),
                )
            )

        if retry_context.quality_gate_failure is not None:
            quality_failure = retry_context.quality_gate_failure
            for violation in quality_failure.violations:
                records.append(
                    LessonRecord(
                        recorded_at=recorded_at,
                        lesson_kind="quality_gate",
                        repository=repository,
                        run_label=run_label,
                        issue_key=issue_key,
                        source_attempt_number=retry_context.source_attempt_number,
                        issue_rule_id=issue_rule_id,
                        failure_kind=retry_context.failure_kind or "quality_gate",
                        primary_failure_fingerprint=str(
                            getattr(retry_context, "primary_failure_fingerprint", "") or ""
                        ).strip(),
                        failure_fingerprints=self._dedupe(getattr(retry_context, "failure_fingerprints", ()) or ()),
                        scope_mode=scope_mode,
                        guardrail_mode=guardrail_mode,
                        boundary_failure_code=(
                            retry_context.boundary_failure.code
                            if retry_context.boundary_failure is not None
                            else ""
                        ),
                        summary=violation.message or quality_failure.summary,
                        guidance=self._dedupe((violation.retry_hint,)),
                        evidence=self._dedupe((violation.evidence, violation.symbol)),
                        quality_gate_rule_ids=self._dedupe((violation.rule_id,)),
                    )
                )

        return tuple(records)

    def _update_pattern_file(self, path: Path, records: tuple[LessonRecord, ...]) -> None:
        patterns = self._load_patterns(path)
        for record in records:
            pattern_key = self._pattern_key(record)
            current = patterns.get(pattern_key)
            if current is None:
                patterns[pattern_key] = LessonPattern(
                    pattern_key=pattern_key,
                    lesson_kind=record.lesson_kind,
                    issue_rule_id=record.issue_rule_id,
                    failure_kind=record.failure_kind,
                    primary_failure_fingerprint=record.primary_failure_fingerprint,
                    failure_fingerprints=record.failure_fingerprints,
                    scope_mode=record.scope_mode,
                    guardrail_mode=record.guardrail_mode,
                    boundary_failure_code=record.boundary_failure_code,
                    count=1,
                    first_seen_at=record.recorded_at,
                    last_seen_at=record.recorded_at,
                    latest_summary=record.summary,
                    guidance=record.guidance,
                    evidence=record.evidence,
                    quality_gate_rule_ids=record.quality_gate_rule_ids,
                )
                continue

            patterns[pattern_key] = LessonPattern(
                pattern_key=current.pattern_key,
                lesson_kind=current.lesson_kind,
                issue_rule_id=current.issue_rule_id,
                failure_kind=current.failure_kind,
                primary_failure_fingerprint=(
                    current.primary_failure_fingerprint or record.primary_failure_fingerprint
                ),
                failure_fingerprints=self._dedupe(
                    (*current.failure_fingerprints, *record.failure_fingerprints)
                ),
                scope_mode=current.scope_mode,
                guardrail_mode=current.guardrail_mode,
                boundary_failure_code=current.boundary_failure_code or record.boundary_failure_code,
                count=current.count + 1,
                first_seen_at=current.first_seen_at or record.recorded_at,
                last_seen_at=record.recorded_at,
                latest_summary=record.summary or current.latest_summary,
                guidance=self._dedupe((*current.guidance, *record.guidance)),
                evidence=self._dedupe((*current.evidence, *record.evidence)),
                quality_gate_rule_ids=self._dedupe(
                    (*current.quality_gate_rule_ids, *record.quality_gate_rule_ids)
                ),
            )
        self._save_patterns(path, patterns)

    def record_failure(
        self,
        *,
        repository: str,
        run_label: str,
        issue_key: str,
        issue_rule_id: str,
        retry_context: RetryContext | None,
        scope_mode: str = "",
        guardrail_mode: str = "",
        quality_gate_rule_ids: tuple[str, ...] = (),
    ) -> None:
        """Persist structured lessons for one failed attempt."""

        if retry_context is None or not retry_context.failure_kind:
            return

        records = self._build_records(
            repository=repository,
            run_label=run_label,
            issue_key=issue_key,
            issue_rule_id=issue_rule_id,
            retry_context=retry_context,
            scope_mode=scope_mode,
            guardrail_mode=guardrail_mode,
            quality_gate_rule_ids=quality_gate_rule_ids,
        )
        if not records:
            return

        quality_records = tuple(record for record in records if record.lesson_kind == "quality_gate")
        for record in quality_records:
            self._append_jsonl(self.quality_gate_lessons_path, record.to_dict())

        boundary_records = tuple(record for record in records if record.lesson_kind == "boundary")
        if boundary_records:
            self._update_pattern_file(self.boundary_patterns_path, boundary_records)

        rule_records = tuple(record for record in records if record.lesson_kind == "rule_failure")
        if rule_records:
            self._update_pattern_file(self.rule_patterns_path, rule_records)

    def _load_quality_gate_lessons(self) -> list[LessonRecord]:
        if not self.quality_gate_lessons_path.exists():
            return []
        records: list[LessonRecord] = []
        for raw_line in self.quality_gate_lessons_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            records.append(
                LessonRecord(
                    recorded_at=str(item.get("recorded_at", "")).strip(),
                    lesson_kind=str(item.get("lesson_kind", "")).strip(),
                    repository=str(item.get("repository", "")).strip(),
                    run_label=str(item.get("run_label", "")).strip(),
                    issue_key=str(item.get("issue_key", "")).strip(),
                    source_attempt_number=int(item.get("source_attempt_number", 0) or 0),
                    issue_rule_id=str(item.get("issue_rule_id", "")).strip(),
                    failure_kind=str(item.get("failure_kind", "")).strip(),
                    primary_failure_fingerprint=str(item.get("primary_failure_fingerprint", "")).strip(),
                    failure_fingerprints=self._dedupe(tuple(item.get("failure_fingerprints", ()))),
                    scope_mode=str(item.get("scope_mode", "")).strip(),
                    guardrail_mode=str(item.get("guardrail_mode", "")).strip(),
                    boundary_failure_code=str(item.get("boundary_failure_code", "")).strip(),
                    summary=str(item.get("summary", "")).strip(),
                    guidance=self._dedupe(tuple(item.get("guidance", ()))),
                    evidence=self._dedupe(tuple(item.get("evidence", ()))),
                    quality_gate_rule_ids=self._dedupe(tuple(item.get("quality_gate_rule_ids", ()))),
                )
            )
        return records

    @staticmethod
    def _pattern_to_planner_lesson(
        pattern: LessonPattern,
        source: str,
        *,
        selection_mode: str,
        selection_reason: str,
    ) -> PlannerLesson:
        return PlannerLesson(
            source=source,
            summary=pattern.latest_summary or f"Repeated {pattern.failure_kind} pattern recorded.",
            guidance=pattern.guidance,
            issue_rule_id=pattern.issue_rule_id,
            failure_kind=pattern.failure_kind,
            primary_failure_fingerprint=pattern.primary_failure_fingerprint,
            failure_fingerprints=pattern.failure_fingerprints,
            scope_mode=pattern.scope_mode,
            guardrail_mode=pattern.guardrail_mode,
            boundary_failure_code=pattern.boundary_failure_code,
            quality_gate_rule_ids=pattern.quality_gate_rule_ids,
            selection_mode=selection_mode,
            selection_reason=selection_reason,
            count=pattern.count,
        )

    def load_planner_lessons(
        self,
        *,
        issue_rule_id: str,
        failure_kind: str = "",
        failure_fingerprints: tuple[str, ...] = (),
        scope_mode: str = "",
        guardrail_mode: str = "",
        boundary_failure_code: str = "",
        quality_gate_rule_ids: tuple[str, ...] = (),
        limit: int = 2,
    ) -> tuple[PlannerLesson, ...]:
        """Load the most relevant structured lessons for the next planner step."""

        lessons: list[PlannerLesson] = []
        normalized_quality_gate_rule_ids = {
            str(item).strip()
            for item in quality_gate_rule_ids
            if str(item).strip()
        }
        normalized_failure_fingerprints = tuple(
            dict.fromkeys(
                str(item).strip()
                for item in failure_fingerprints
                if str(item).strip()
            )
        )
        primary_failure_fingerprint = normalized_failure_fingerprints[0] if normalized_failure_fingerprints else ""

        boundary_patterns = self._load_patterns(self.boundary_patterns_path)
        exact_boundary = [
            pattern
            for pattern in boundary_patterns.values()
            if pattern.issue_rule_id == issue_rule_id
            and primary_failure_fingerprint
            and pattern.primary_failure_fingerprint == primary_failure_fingerprint
            and (not failure_kind or pattern.failure_kind == failure_kind)
            and (not scope_mode or pattern.scope_mode == scope_mode)
            and (not guardrail_mode or pattern.guardrail_mode == guardrail_mode)
            and (not boundary_failure_code or pattern.boundary_failure_code == boundary_failure_code)
        ]
        exact_boundary.sort(key=lambda item: (item.count, item.last_seen_at), reverse=True)
        lessons.extend(
            self._pattern_to_planner_lesson(
                pattern,
                "boundary_pattern",
                selection_mode="rule_plus_fingerprint",
                selection_reason=f"rule_id={issue_rule_id} and failure_fingerprint={primary_failure_fingerprint}",
            )
            for pattern in exact_boundary[:1]
        )

        matching_boundary = [
            pattern
            for pattern in boundary_patterns.values()
            if pattern.issue_rule_id == issue_rule_id
            and (not failure_kind or pattern.failure_kind == failure_kind)
            and (not scope_mode or pattern.scope_mode == scope_mode)
            and (not guardrail_mode or pattern.guardrail_mode == guardrail_mode)
            and (not boundary_failure_code or pattern.boundary_failure_code == boundary_failure_code)
        ]
        matching_boundary.sort(key=lambda item: (item.count, item.last_seen_at), reverse=True)
        lessons.extend(
            self._pattern_to_planner_lesson(
                pattern,
                "boundary_pattern",
                selection_mode="rule_exact",
                selection_reason=f"rule_id={issue_rule_id} and failure_kind={failure_kind or '-'}",
            )
            for pattern in matching_boundary[:1]
        )

        rule_patterns = self._load_patterns(self.rule_patterns_path)
        exact_rule_patterns = [
            pattern
            for pattern in rule_patterns.values()
            if pattern.issue_rule_id == issue_rule_id
            and primary_failure_fingerprint
            and pattern.primary_failure_fingerprint == primary_failure_fingerprint
            and (not failure_kind or pattern.failure_kind == failure_kind)
        ]
        exact_rule_patterns.sort(key=lambda item: (item.count, item.last_seen_at), reverse=True)
        lessons.extend(
            self._pattern_to_planner_lesson(
                pattern,
                "rule_pattern",
                selection_mode="rule_plus_fingerprint",
                selection_reason=f"rule_id={issue_rule_id} and failure_fingerprint={primary_failure_fingerprint}",
            )
            for pattern in exact_rule_patterns[:1]
        )

        if normalized_quality_gate_rule_ids and len(lessons) < limit:
            quality_lessons = [
                record
                for record in self._load_quality_gate_lessons()
                if record.issue_rule_id == issue_rule_id
                and normalized_quality_gate_rule_ids.intersection(record.quality_gate_rule_ids)
            ]
            quality_lessons.sort(key=lambda item: item.recorded_at, reverse=True)
            seen_quality_rules: set[str] = set()
            for record in quality_lessons:
                quality_rule_id = next(iter(record.quality_gate_rule_ids), "")
                if quality_rule_id and quality_rule_id in seen_quality_rules:
                    continue
                if quality_rule_id:
                    seen_quality_rules.add(quality_rule_id)
                lessons.append(
                    PlannerLesson(
                        source="quality_gate_lesson",
                        summary=record.summary,
                        guidance=record.guidance,
                        issue_rule_id=record.issue_rule_id,
                        failure_kind=record.failure_kind,
                        primary_failure_fingerprint=record.primary_failure_fingerprint,
                        failure_fingerprints=record.failure_fingerprints,
                        scope_mode=record.scope_mode,
                        guardrail_mode=record.guardrail_mode,
                        boundary_failure_code=record.boundary_failure_code,
                        quality_gate_rule_ids=record.quality_gate_rule_ids,
                        selection_mode="rule_exact",
                        selection_reason=(
                            "quality_gate_rules=" + ",".join(sorted(normalized_quality_gate_rule_ids))
                        ),
                        count=1,
                    )
                )
                if len(lessons) >= limit:
                    break

        matching_rule_patterns = [
            pattern
            for pattern in rule_patterns.values()
            if pattern.issue_rule_id == issue_rule_id
            and (not failure_kind or pattern.failure_kind == failure_kind)
        ]
        matching_rule_patterns.sort(key=lambda item: (item.count, item.last_seen_at), reverse=True)
        lessons.extend(
            self._pattern_to_planner_lesson(
                pattern,
                "rule_pattern",
                selection_mode="rule_exact",
                selection_reason=f"rule_id={issue_rule_id} and failure_kind={failure_kind or '-'}",
            )
            for pattern in matching_rule_patterns[:1]
        )

        deduped: list[PlannerLesson] = []
        seen_keys: set[tuple[str, str]] = set()
        for lesson in lessons:
            key = (lesson.source, lesson.summary)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduped.append(lesson)
            if len(deduped) >= limit:
                break
        return tuple(deduped)
