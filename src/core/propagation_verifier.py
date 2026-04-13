"""Build-precheck verification for bounded signature propagation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PropagationCheckResult:
    """Structured propagation verification outcome."""

    status: str
    summary: str
    residual_targets: tuple[str, ...] = ()

    def to_retry_message(self) -> str:
        if self.status != "retry":
            return ""
        lines = [
            "Propagation verifier rejected the patch because signature updates are incomplete.",
            self.summary,
        ]
        if self.residual_targets:
            lines.append("Residual Targets:")
            for index, target in enumerate(self.residual_targets, start=1):
                lines.append(f"{index}. {target}")
        return "\n".join(lines)


class PropagationVerifier:
    """Verify that bounded signature propagation finished before build runs."""

    @staticmethod
    def _normalize_path(file_path: str) -> str:
        return str(file_path or "").replace("\\", "/").lstrip("/")

    @classmethod
    def review(
        cls,
        *,
        workspace_path: Path,
        edit_contract,
        issue_file_path: str,
        current_issue_file_content: str | None = None,
    ) -> PropagationCheckResult:
        repair_plan = getattr(edit_contract, "repair_plan", None)
        if repair_plan is None or not bool(getattr(repair_plan, "requires_signature_change", False)):
            return PropagationCheckResult(
                status="pass",
                summary="No signature propagation verification was required for this attempt.",
            )
        if str(getattr(repair_plan, "selected_archetype", "") or "").strip() == "declaration_hygiene":
            return PropagationCheckResult(
                status="pass",
                summary="Declaration-hygiene cleanup does not require signature propagation verification.",
            )

        proposed_method_name = str(getattr(repair_plan, "proposed_method_name", "") or "").strip()
        verification_targets = tuple(getattr(repair_plan, "verification_targets", ()) or ())
        if not proposed_method_name or not verification_targets:
            return PropagationCheckResult(
                status="pass",
                summary="No explicit propagation verification targets were declared for this attempt.",
            )

        normalized_issue_path = cls._normalize_path(issue_file_path)
        residual_targets: list[str] = []
        file_cache: dict[str, list[str] | None] = {}

        for target in verification_targets:
            normalized_target_path = cls._normalize_path(getattr(target, "file", ""))
            if normalized_target_path not in file_cache:
                if normalized_target_path == normalized_issue_path and current_issue_file_content is not None:
                    file_cache[normalized_target_path] = current_issue_file_content.splitlines()
                else:
                    target_path = workspace_path / Path(normalized_target_path)
                    if target_path.exists():
                        file_cache[normalized_target_path] = target_path.read_text(encoding="utf-8").splitlines()
                    else:
                        file_cache[normalized_target_path] = None
            lines = file_cache.get(normalized_target_path)
            if lines is None:
                residual_targets.append(
                    f"{normalized_target_path} ({getattr(target, 'kind', 'target')}) missing from workspace"
                )
                continue

            start_line = int(getattr(target, "start_line", 0) or 0)
            end_line = int(getattr(target, "end_line", 0) or 0)
            if start_line > 0 and end_line > 0:
                snippet = "\n".join(lines[max(0, start_line - 1): min(len(lines), end_line)])
            else:
                snippet = "\n".join(lines)

            if proposed_method_name not in snippet:
                label = f"{normalized_target_path}:{start_line}-{end_line}" if start_line and end_line else normalized_target_path
                residual_targets.append(
                    f"{label} ({getattr(target, 'kind', 'target')}) still missing `{proposed_method_name}`"
                )

        if residual_targets:
            return PropagationCheckResult(
                status="retry",
                summary=(
                    f"Expected `{proposed_method_name}` to appear in {len(verification_targets)} verification target(s), "
                    f"but {len(residual_targets)} target(s) still look stale."
                ),
                residual_targets=tuple(residual_targets),
            )

        return PropagationCheckResult(
            status="pass",
            summary=(
                f"Verified `{proposed_method_name}` across {len(verification_targets)} propagation target(s) before build."
            ),
        )
