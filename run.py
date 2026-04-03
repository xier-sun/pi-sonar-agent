"""One-command entrypoint for local users."""

from __future__ import annotations

import sys
from pathlib import Path


def _bootstrap_import_path() -> None:
    root = Path(__file__).resolve().parent
    src_dir = root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))


def _print_missing_dependency_help(missing: str) -> None:
    print(f"[ERROR] 缺少依赖模块: {missing}")
    print("[HINT] 请先安装项目依赖后再运行：")
    print("  python -m pip install -e .")


def main() -> None:
    _bootstrap_import_path()
    try:
        from pi_sonar_agent.main import main as app_main
    except ModuleNotFoundError as exc:
        missing = getattr(exc, "name", "") or str(exc)
        _print_missing_dependency_help(missing)
        raise SystemExit(1) from exc

    try:
        app_main()
    except ModuleNotFoundError as exc:
        missing = getattr(exc, "name", "") or str(exc)
        _print_missing_dependency_help(missing)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
