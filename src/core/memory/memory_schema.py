"""Schema validation helpers for runtime working-memory artifacts."""

from __future__ import annotations

from typing import Any


class MemorySchemaError(ValueError):
    """Raised when a working-memory payload violates the expected schema."""


def ensure_dict(payload: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise MemorySchemaError(f"{field_name} must be an object.")
    return payload


def ensure_string(value: Any, *, field_name: str, required: bool = False) -> str:
    if value is None:
        if required:
            raise MemorySchemaError(f"{field_name} is required.")
        return ""
    text = str(value).strip()
    if required and not text:
        raise MemorySchemaError(f"{field_name} is required.")
    return text


def ensure_int(value: Any, *, field_name: str, default: int = 0) -> int:
    if value in (None, ""):
        return int(default)
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise MemorySchemaError(f"{field_name} must be an integer.") from exc


def ensure_tuple_of_strings(
    value: Any,
    *,
    field_name: str,
) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, tuple):
        items = value
    elif isinstance(value, list):
        items = tuple(value)
    else:
        raise MemorySchemaError(f"{field_name} must be a list of strings.")

    seen: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.append(text)
    return tuple(seen)


def ensure_version(
    value: Any,
    *,
    field_name: str = "version",
    expected: int,
) -> int:
    version = ensure_int(value, field_name=field_name, default=expected)
    if version != expected:
        raise MemorySchemaError(
            f"{field_name}={version} is not supported; expected {expected}."
        )
    return version

