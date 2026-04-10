"""Shared boundary-line policy used by scope and contract review."""

from __future__ import annotations

from collections.abc import Iterable

from pi_sonar_agent.core.boundary_capabilities import normalize_boundary_capabilities
from pi_sonar_agent.core.issue_contract import EditContract


class BoundaryPolicy:
    """Resolve allowed line windows and compare touched lines against them."""

    @staticmethod
    def normalize_line_ranges(
        ranges: Iterable[tuple[int, int]] | tuple[tuple[int, int], ...],
    ) -> tuple[tuple[int, int], ...]:
        normalized: list[tuple[int, int]] = []
        for start_line, end_line in ranges:
            if start_line <= 0 or end_line <= 0:
                continue
            normalized.append((min(start_line, end_line), max(start_line, end_line)))
        return tuple(normalized)

    @classmethod
    def contract_line_ranges(cls, edit_contract: EditContract) -> tuple[tuple[int, int], ...]:
        if edit_contract.allowed_line_ranges:
            return cls.normalize_line_ranges(edit_contract.allowed_line_ranges)
        if len(edit_contract.validation_line_range) == 2:
            return cls.normalize_line_ranges((edit_contract.validation_line_range,))
        return ()

    @staticmethod
    def contract_capabilities(edit_contract: EditContract) -> tuple[str, ...]:
        return normalize_boundary_capabilities(edit_contract.allowed_capabilities)

    @classmethod
    def find_outside_lines(
        cls,
        changed_lines: Iterable[int],
        allowed_line_ranges: Iterable[tuple[int, int]],
    ) -> tuple[int, ...]:
        normalized_ranges = cls.normalize_line_ranges(tuple(allowed_line_ranges))
        if not normalized_ranges:
            return tuple(sorted({line for line in changed_lines if int(line) > 0}))

        outside_lines: list[int] = []
        for raw_line in changed_lines:
            line = int(raw_line)
            if line <= 0:
                continue
            if any(start_line <= line <= end_line for start_line, end_line in normalized_ranges):
                continue
            outside_lines.append(line)
        return tuple(sorted(dict.fromkeys(outside_lines)))
