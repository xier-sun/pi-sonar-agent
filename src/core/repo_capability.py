"""Detect coarse-grained .NET / C# repository capability facts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree


def _local_name(tag: str) -> str:
    return str(tag or "").rsplit("}", 1)[-1]


def _split_values(raw: str) -> tuple[str, ...]:
    return tuple(
        item.strip()
        for item in str(raw or "").split(";")
        if str(item or "").strip()
    )


def _parse_major_version(value: str) -> int:
    text = str(value or "").strip().lower()
    if not text:
        return 0
    if text in {"latest", "preview"}:
        return 999
    if text == "default":
        return 0
    digits = []
    for char in text:
        if char.isdigit():
            digits.append(char)
        elif digits:
            break
    return int("".join(digits)) if digits else 0


def _framework_major_version(framework: str) -> int:
    text = str(framework or "").strip().lower()
    if text.startswith("netcoreapp"):
        return _parse_major_version(text.replace("netcoreapp", "", 1))
    if text.startswith("netstandard"):
        return _parse_major_version(text.replace("netstandard", "", 1))
    if text.startswith("net"):
        return _parse_major_version(text.replace("net", "", 1))
    return 0


@dataclass(frozen=True)
class RepoCapabilityProfile:
    """Coarse repository capability facts used by planner/prompt layers."""

    target_frameworks: tuple[str, ...]
    lang_version: str
    nullable: str
    implicit_usings: str
    supports_record: bool
    supports_init_only: bool
    supports_required: bool
    supports_file_scoped_namespace: bool
    supports_global_using: bool
    evidence_files: tuple[str, ...]

    def unsupported_language_features(self) -> tuple[str, ...]:
        """Return the C# features that should be blocked for this repository."""

        blocked: list[str] = []
        if not self.supports_record:
            blocked.append("record")
        if not self.supports_init_only:
            blocked.append("init")
        if not self.supports_required:
            blocked.append("required")
        if not self.supports_file_scoped_namespace:
            blocked.append("file-scoped namespace")
        if not self.supports_global_using:
            blocked.append("global using")
        return tuple(blocked)

    def to_dict(self) -> dict[str, object]:
        return {
            "target_frameworks": list(self.target_frameworks),
            "lang_version": self.lang_version,
            "nullable": self.nullable,
            "implicit_usings": self.implicit_usings,
            "supports_record": self.supports_record,
            "supports_init_only": self.supports_init_only,
            "supports_required": self.supports_required,
            "supports_file_scoped_namespace": self.supports_file_scoped_namespace,
            "supports_global_using": self.supports_global_using,
            "evidence_files": list(self.evidence_files),
        }

    def summary(self) -> str:
        frameworks = ", ".join(self.target_frameworks) or "unknown"
        lang_version = self.lang_version or "default"
        nullable = self.nullable or "unknown"
        return f"TFM={frameworks}; LangVersion={lang_version}; Nullable={nullable}"

    def prompt_hints(self) -> tuple[str, ...]:
        hints = [f"仓库能力指纹：{self.summary()}。"]
        blocked = list(self.unsupported_language_features())
        if blocked:
            hints.append("当前仓库默认禁止引入以下较新 C# 语法：" + ", ".join(blocked) + "。")
        return tuple(hints)


def _iter_project_files(workspace_path: Path) -> tuple[Path, ...]:
    candidates = list(workspace_path.rglob("*.csproj"))
    for extra_name in ("Directory.Build.props", "Directory.Build.targets"):
        candidates.extend(workspace_path.rglob(extra_name))
    return tuple(dict.fromkeys(path for path in candidates if path.is_file()))


def detect_repo_capability(workspace_path: Path) -> RepoCapabilityProfile:
    """Detect coarse capability facts from project/build files."""

    project_files = _iter_project_files(workspace_path)
    frameworks: list[str] = []
    lang_versions: list[str] = []
    nullable_values: list[str] = []
    implicit_usings_values: list[str] = []
    evidence_files: list[str] = []

    for file_path in project_files:
        try:
            root = ElementTree.fromstring(file_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        evidence_files.append(file_path.relative_to(workspace_path).as_posix())
        for element in root.iter():
            name = _local_name(element.tag)
            value = str(element.text or "").strip()
            if not value:
                continue
            if name == "TargetFramework":
                frameworks.extend(_split_values(value))
            elif name == "TargetFrameworks":
                frameworks.extend(_split_values(value))
            elif name == "LangVersion":
                lang_versions.append(value)
            elif name == "Nullable":
                nullable_values.append(value)
            elif name == "ImplicitUsings":
                implicit_usings_values.append(value)

    normalized_frameworks = tuple(dict.fromkeys(frameworks))
    lang_version = lang_versions[-1] if lang_versions else "default"
    nullable = nullable_values[-1] if nullable_values else ""
    implicit_usings = implicit_usings_values[-1] if implicit_usings_values else ""

    lang_major = _parse_major_version(lang_version)
    framework_major = max((_framework_major_version(item) for item in normalized_frameworks), default=0)

    if lang_major <= 0:
        if any(str(item).lower().startswith("netcoreapp3.1") for item in normalized_frameworks):
            lang_major = 8
        elif framework_major >= 7:
            lang_major = 11
        elif framework_major >= 6:
            lang_major = 10
        elif framework_major >= 5:
            lang_major = 9

    supports_record = lang_major >= 9
    supports_init_only = lang_major >= 9
    supports_required = lang_major >= 11
    supports_file_scoped_namespace = lang_major >= 10
    supports_global_using = lang_major >= 10

    return RepoCapabilityProfile(
        target_frameworks=normalized_frameworks,
        lang_version=lang_version,
        nullable=nullable,
        implicit_usings=implicit_usings,
        supports_record=supports_record,
        supports_init_only=supports_init_only,
        supports_required=supports_required,
        supports_file_scoped_namespace=supports_file_scoped_namespace,
        supports_global_using=supports_global_using,
        evidence_files=tuple(dict.fromkeys(evidence_files)),
    )
