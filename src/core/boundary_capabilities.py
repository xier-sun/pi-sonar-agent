"""Shared boundary capability model for issue edit contracts."""

from __future__ import annotations

STATEMENT_EDIT_CAPABILITY = "statement_edit"
DECLARATION_DELETE_CAPABILITY = "declaration_delete"
ADJACENT_CLEANUP_CAPABILITY = "adjacent_cleanup"
METHOD_REWRITE_CAPABILITY = "method_rewrite"
MEMBER_DELETE_CAPABILITY = "member_delete"
METHOD_CLUSTER_DELETE_CAPABILITY = "method_cluster_delete"
HELPER_EXTRACT_CAPABILITY = "helper_extract"
SIGNATURE_CHANGE_CAPABILITY = "signature_change"
NEW_TYPE_ADD_CAPABILITY = "new_type_add"
MULTI_FILE_REFACTOR_CAPABILITY = "multi_file_refactor"

BOUNDARY_PROFILE_STATEMENT_WINDOW = "statement_window"
BOUNDARY_PROFILE_METHOD_WINDOW = "method_window"
BOUNDARY_PROFILE_CONTROL_BLOCK = "control_block"
BOUNDARY_PROFILE_DECLARATION_COMMENT = "declaration_comment"
BOUNDARY_PROFILE_CONDITIONAL_CHAIN = "conditional_chain"
BOUNDARY_PROFILE_EXPRESSION_REWRITE = "expression_rewrite"
BOUNDARY_PROFILE_LOOP_REWRITE = "loop_rewrite"
BOUNDARY_PROFILE_DECLARATION_ANCHOR = "declaration_anchor"
BOUNDARY_PROFILE_COMMENT_ADJACENT_CLEANUP = "comment_adjacent_cleanup"
BOUNDARY_PROFILE_MEMBER_CLUSTER = "member_cluster"

_STATEMENT_SCOPE_MODE = "statement"
_METHOD_SCOPE_MODE = "method"
_CONTROL_BLOCK_SCOPE_MODE = "control_block"
_DECLARATION_COMMENT_SCOPE_MODE = "declaration_comment"
_CONDITIONAL_CHAIN_SCOPE_MODE = "conditional_chain"
_EXPRESSION_REWRITE_SCOPE_MODE = "expression_rewrite"
_LOOP_REWRITE_SCOPE_MODE = "loop_rewrite"

_DEFAULT_PROFILE_BY_SCOPE_MODE = {
    _STATEMENT_SCOPE_MODE: BOUNDARY_PROFILE_STATEMENT_WINDOW,
    _METHOD_SCOPE_MODE: BOUNDARY_PROFILE_METHOD_WINDOW,
    _CONTROL_BLOCK_SCOPE_MODE: BOUNDARY_PROFILE_CONTROL_BLOCK,
    _DECLARATION_COMMENT_SCOPE_MODE: BOUNDARY_PROFILE_DECLARATION_COMMENT,
    _CONDITIONAL_CHAIN_SCOPE_MODE: BOUNDARY_PROFILE_CONDITIONAL_CHAIN,
    _EXPRESSION_REWRITE_SCOPE_MODE: BOUNDARY_PROFILE_EXPRESSION_REWRITE,
    _LOOP_REWRITE_SCOPE_MODE: BOUNDARY_PROFILE_LOOP_REWRITE,
}

_DEFAULT_CAPABILITIES_BY_SCOPE_MODE = {
    _STATEMENT_SCOPE_MODE: (STATEMENT_EDIT_CAPABILITY,),
    _METHOD_SCOPE_MODE: (METHOD_REWRITE_CAPABILITY, HELPER_EXTRACT_CAPABILITY),
    _CONTROL_BLOCK_SCOPE_MODE: (STATEMENT_EDIT_CAPABILITY,),
    _DECLARATION_COMMENT_SCOPE_MODE: (STATEMENT_EDIT_CAPABILITY,),
    _CONDITIONAL_CHAIN_SCOPE_MODE: (STATEMENT_EDIT_CAPABILITY,),
    _EXPRESSION_REWRITE_SCOPE_MODE: (STATEMENT_EDIT_CAPABILITY,),
    _LOOP_REWRITE_SCOPE_MODE: (METHOD_REWRITE_CAPABILITY,),
}


def normalize_boundary_capabilities(capabilities: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Normalize capability names while preserving declaration order."""

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_value in capabilities:
        value = str(raw_value or "").strip()
        if not value or value in seen:
            continue
        normalized.append(value)
        seen.add(value)
    return tuple(normalized)


def resolve_boundary_profile(scope_mode: str, explicit_profile: str = "") -> str:
    """Resolve the effective boundary profile for a scope mode."""

    normalized_profile = str(explicit_profile or "").strip()
    if normalized_profile:
        return normalized_profile
    return _DEFAULT_PROFILE_BY_SCOPE_MODE.get(
        str(scope_mode or "").strip(),
        BOUNDARY_PROFILE_STATEMENT_WINDOW,
    )


def resolve_boundary_capabilities(
    scope_mode: str,
    explicit_capabilities: tuple[str, ...] | list[str] = (),
) -> tuple[str, ...]:
    """Resolve the effective boundary capabilities for a scope mode."""

    defaults = _DEFAULT_CAPABILITIES_BY_SCOPE_MODE.get(
        str(scope_mode or "").strip(),
        (STATEMENT_EDIT_CAPABILITY,),
    )
    return normalize_boundary_capabilities((*defaults, *explicit_capabilities))
