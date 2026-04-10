"""Attempt-local context cache for host-side file/snippet reads."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CachedFileContent:
    """Cached file text and split lines for one attempt."""

    path: str
    mtime_ns: int
    text: str
    lines: tuple[str, ...]


class AttemptContextCache:
    """Cache host-side file content and rendered windows for one attempt."""

    def __init__(self) -> None:
        self._files: dict[str, CachedFileContent] = {}
        self._windows: dict[tuple[str, int, int], str] = {}

    @staticmethod
    def _normalize_path(path: str | Path) -> str:
        return str(path).replace("\\", "/")

    @staticmethod
    def _file_mtime_ns(path: Path) -> int:
        return int(path.stat().st_mtime_ns)

    def invalidate(self, path: str | Path) -> None:
        """Invalidate one file and any cached windows derived from it."""

        normalized = self._normalize_path(path)
        self._files.pop(normalized, None)
        for key in tuple(self._windows):
            if key[0] == normalized:
                self._windows.pop(key, None)

    def read_text(self, path: str | Path, *, encoding: str = "utf-8") -> str:
        """Read file text using attempt-local caching."""

        normalized = self._normalize_path(path)
        file_path = Path(path)
        current_mtime = self._file_mtime_ns(file_path)
        cached = self._files.get(normalized)
        if cached is not None and cached.mtime_ns == current_mtime:
            return cached.text

        text = file_path.read_text(encoding=encoding)
        lines = tuple(text.splitlines())
        self._files[normalized] = CachedFileContent(
            path=normalized,
            mtime_ns=current_mtime,
            text=text,
            lines=lines,
        )
        return text

    def read_lines(self, path: str | Path, *, encoding: str = "utf-8") -> tuple[str, ...]:
        """Read file lines using attempt-local caching."""

        normalized = self._normalize_path(path)
        file_path = Path(path)
        current_mtime = self._file_mtime_ns(file_path)
        cached = self._files.get(normalized)
        if cached is not None and cached.mtime_ns == current_mtime:
            return cached.lines

        self.read_text(file_path, encoding=encoding)
        refreshed = self._files[normalized]
        return refreshed.lines

    def render_numbered_window(
        self,
        path: str | Path,
        start_line: int,
        end_line: int,
        *,
        encoding: str = "utf-8",
    ) -> str:
        """Render a numbered code window and cache it for the attempt."""

        normalized = self._normalize_path(path)
        normalized_start = max(1, min(start_line, end_line))
        normalized_end = max(normalized_start, max(start_line, end_line))
        window_key = (normalized, normalized_start, normalized_end)
        cached_window = self._windows.get(window_key)
        if cached_window is not None:
            return cached_window

        lines = self.read_lines(path, encoding=encoding)
        if not lines:
            return ""
        bounded_end = min(len(lines), normalized_end)
        rendered = "\n".join(
            f"{line_number:4d} | {lines[line_number - 1]}"
            for line_number in range(normalized_start, bounded_end + 1)
        )
        self._windows[window_key] = rendered
        return rendered
