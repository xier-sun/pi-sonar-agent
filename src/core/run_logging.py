"""Run-level console logging helpers."""

from __future__ import annotations

import io
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path


class TeeStream(io.TextIOBase):
    """Mirror writes to multiple text streams."""

    def __init__(self, *streams: io.TextIOBase):
        self._streams = streams

    @property
    def encoding(self) -> str:
        return getattr(self._streams[0], "encoding", "utf-8")

    def write(self, text: str) -> int:
        for stream in self._streams:
            try:
                stream.write(text)
                stream.flush()
            except ValueError:
                continue
        return len(text)

    def flush(self) -> None:
        for stream in self._streams:
            try:
                stream.flush()
            except ValueError:
                continue

    def isatty(self) -> bool:
        return bool(getattr(self._streams[0], "isatty", lambda: False)())

    def writable(self) -> bool:
        return True


class RunLogSession:
    """Tee stdout/stderr to a per-run log file."""

    def __init__(
        self,
        *,
        run_label: str | None = None,
        log_root: str = "logs/runs",
        prefix: str = "run",
    ) -> None:
        self.run_label = run_label or time.strftime("%Y%m%d%H%M%S")
        self.log_path = Path(log_root) / f"{prefix}_{self.run_label}.log"
        self._file: io.TextIOWrapper | None = None
        self._original_stdout: io.TextIOBase | None = None
        self._original_stderr: io.TextIOBase | None = None

    def __enter__(self) -> RunLogSession:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.log_path.open("a", encoding="utf-8", buffering=1)
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr
        sys.stdout = TeeStream(self._original_stdout, self._file)
        sys.stderr = TeeStream(self._original_stderr, self._file)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._original_stdout is not None:
            sys.stdout = self._original_stdout
        if self._original_stderr is not None:
            sys.stderr = self._original_stderr
        if self._file is not None:
            self._file.flush()
            self._file.close()


def run_command_logged(
    command: str,
    *,
    cwd: Path | None = None,
    timeout: int | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a shell command and mirror any output through the current console."""

    result = subprocess.run(
        command,
        shell=True,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()

    if stdout:
        print(stdout)
    if stderr:
        print(stderr, file=sys.stderr)

    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            command,
            output=result.stdout,
            stderr=result.stderr,
        )

    return result


def format_removed_workspaces(paths: Sequence[Path]) -> list[str]:
    """Format removed workspace paths for user-facing logging."""

    return [str(path).replace("\\", "/") for path in paths]
