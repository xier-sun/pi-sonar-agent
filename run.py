"""One-command entrypoint for local users.

Usage:
  python run.py --project-key ... --repository ... --author ...
"""

from __future__ import annotations

import sys
from pathlib import Path


def _bootstrap_import_path() -> None:
    root = Path(__file__).resolve().parent
    src_dir = root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    # Local fallback for users who cloned claude-agent-sdk-python nearby.
    sdk_src = Path("/Users/pubian/claude-agent-sdk-python/src")
    if sdk_src.exists() and str(sdk_src) not in sys.path:
        sys.path.insert(0, str(sdk_src))


def main() -> None:
    _bootstrap_import_path()
    try:
        from pi_sonar_agent.main import main as app_main
    except ModuleNotFoundError as exc:
        missing = getattr(exc, "name", "") or str(exc)
        print(f"[ERROR] 缺少依赖模块: {missing}")
        print("[HINT] 先安装依赖后再运行：")
        print("  python3 -m pip install -e /Users/pubian/claude-agent-sdk-python")
        print("  python3 -m pip install mcp python-dotenv requests anyio typer rich mysql-connector-python")
        raise SystemExit(1) from exc

    try:
        app_main()
    except ModuleNotFoundError as exc:
        missing = getattr(exc, "name", "") or str(exc)
        print(f"[ERROR] 缺少依赖模块: {missing}")
        print("[HINT] 先安装依赖后再运行：")
        print("  python3 -m pip install -e /Users/pubian/claude-agent-sdk-python")
        print("  python3 -m pip install mcp python-dotenv requests anyio typer rich mysql-connector-python")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
