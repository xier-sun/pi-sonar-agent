"""MCP Tool Server for SonarQube Fix Agent.

This module provides MCP tools for:
- Reading source files
- Applying code edits
- Running build commands
- Git operations (commit, push, create PR)
"""

import asyncio
import os
import subprocess
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool


# ============== File Operations Tools ==============

@tool(
    "read_file",
    "Read the contents of a source file",
    {
        "file_path": str,
        "start_line": int | None,
        "end_line": int | None,
    },
)
async def read_file(args: dict[str, Any]) -> dict[str, Any]:
    """Read file contents with optional line range."""
    file_path = Path(args["file_path"])

    if not file_path.exists():
        return {
            "content": [{"type": "text", "text": f"Error: File not found: {file_path}"}],
            "is_error": True,
        }

    try:
        content = file_path.read_text(encoding="utf-8")
        lines = content.split("\n")

        start = args.get("start_line", 1) or 1
        end = args.get("end_line", len(lines)) or len(lines)

        # Convert to 0-indexed
        start_idx = max(0, start - 1)
        end_idx = min(len(lines), end)

        selected_lines = lines[start_idx:end_idx]
        selected_content = "\n".join(selected_lines)

        # Add line numbers for reference
        numbered_lines = [
            f"{start_idx + i + 1:4d} | {line}"
            for i, line in enumerate(selected_lines)
        ]
        numbered_content = "\n".join(numbered_lines)

        return {
            "content": [
                {
                    "type": "text",
                    "text": f"File: {file_path}\nLines: {start}-{end}\n\n{numbered_content}",
                }
            ]
        }
    except Exception as e:
        return {
            "content": [{"type": "text", "text": f"Error reading file: {e}"}],
            "is_error": True,
        }


@tool(
    "apply_edit",
    "Apply an edit to a source file using old_text/new_text replacement",
    {
        "file_path": str,
        "old_text": str,
        "new_text": str,
    },
)
async def apply_edit(args: dict[str, Any]) -> dict[str, Any]:
    """Apply a precise edit to a file using text replacement."""
    file_path = Path(args["file_path"])
    old_text = args["old_text"]
    new_text = args["new_text"]

    if not file_path.exists():
        return {
            "content": [{"type": "text", "text": f"Error: File not found: {file_path}"}],
            "is_error": True,
        }

    try:
        content = file_path.read_text(encoding="utf-8")

        # Find the old text - must be unique
        count = content.count(old_text)
        if count == 0:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Error: Could not find the old text in file.\n\nOld text:\n```\n{old_text}\n```",
                    }
                ],
                "is_error": True,
            }
        if count > 1:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Error: Found {count} occurrences of the old text. Make it unique.",
                    }
                ],
                "is_error": True,
            }

        # Apply the edit
        new_content = content.replace(old_text, new_text, 1)

        # Validate the edit
        if old_text == new_text:
            return {
                "content": [{"type": "text", "text": "Error: No actual change made."}],
                "is_error": True,
            }

        # Write the file
        file_path.write_text(new_content, encoding="utf-8")

        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Successfully edited {file_path}\n\nOld:\n```\n{old_text}\n```\n\nNew:\n```\n{new_text}\n```",
                }
            ]
        }
    except Exception as e:
        return {
            "content": [{"type": "text", "text": f"Error applying edit: {e}"}],
            "is_error": True,
        }


@tool(
    "create_file",
    "Create a new file with the given content",
    {
        "file_path": str,
        "content": str,
    },
)
async def create_file(args: dict[str, Any]) -> dict[str, Any]:
    """Create a new file."""
    file_path = Path(args["file_path"])
    content = args["content"]

    if file_path.exists():
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Error: File already exists: {file_path}",
                }
            ],
            "is_error": True,
        }

    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")

        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Successfully created {file_path}",
                }
            ]
        }
    except Exception as e:
        return {
            "content": [{"type": "text", "text": f"Error creating file: {e}"}],
            "is_error": True,
        }


# ============== Build Tools ==============

@tool(
    "run_build",
    "Run a build command (dotnet build, msbuild, etc.)",
    {
        "command": str,
        "cwd": str | None,
        "timeout": int | None,
    },
)
async def run_build(args: dict[str, Any]) -> dict[str, Any]:
    """Run a build command to verify the code compiles."""
    command = args["command"]
    cwd = args.get("cwd") or os.getcwd()
    timeout = args.get("timeout") or 300  # 5 minutes default

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )

        output = f"Exit code: {result.returncode}\n\n"
        output += f"=== STDOUT ===\n{result.stdout}\n"
        if result.stderr:
            output += f"\n=== STDERR ===\n{result.stderr}"

        return {
            "content": [{"type": "text", "text": output}],
            "is_error": result.returncode != 0,
        }
    except subprocess.TimeoutExpired:
        return {
            "content": [{"type": "text", "text": f"Build timed out after {timeout} seconds"}],
            "is_error": True,
        }
    except Exception as e:
        return {
            "content": [{"type": "text", "text": f"Error running build: {e}"}],
            "is_error": True,
        }


@tool(
    "run_tests",
    "Run test command (dotnet test, etc.)",
    {
        "command": str,
        "cwd": str | None,
        "timeout": int | None,
    },
)
async def run_tests(args: dict[str, Any]) -> dict[str, Any]:
    """Run tests to verify the fix works correctly."""
    command = args["command"]
    cwd = args.get("cwd") or os.getcwd()
    timeout = args.get("timeout") or 600  # 10 minutes default

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )

        output = f"Exit code: {result.returncode}\n\n"
        output += f"=== STDOUT ===\n{result.stdout}\n"
        if result.stderr:
            output += f"\n=== STDERR ===\n{result.stderr}"

        return {
            "content": [{"type": "text", "text": output}],
            "is_error": result.returncode != 0,
        }
    except subprocess.TimeoutExpired:
        return {
            "content": [{"type": "text", "text": f"Tests timed out after {timeout} seconds"}],
            "is_error": True,
        }
    except Exception as e:
        return {
            "content": [{"type": "text", "text": f"Error running tests: {e}"}],
            "is_error": True,
        }


# ============== Git Tools ==============

@tool(
    "git_status",
    "Show git status of the repository",
    {
        "cwd": str | None,
    },
)
async def git_status(args: dict[str, Any]) -> dict[str, Any]:
    """Show git status."""
    cwd = args.get("cwd") or os.getcwd()

    try:
        result = subprocess.run(
            "git status",
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )

        return {
            "content": [
                {
                    "type": "text",
                    "text": result.stdout or result.stderr,
                }
            ]
        }
    except Exception as e:
        return {
            "content": [{"type": "text", "text": f"Error: {e}"}],
            "is_error": True,
        }


@tool(
    "git_add",
    "Stage files for commit",
    {
        "files": str,
        "cwd": str | None,
    },
)
async def git_add(args: dict[str, Any]) -> dict[str, Any]:
    """Stage files for commit."""
    files = args["files"]
    cwd = args.get("cwd") or os.getcwd()

    try:
        result = subprocess.run(
            f"git add {files}",
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )

        return {
            "content": [
                {
                    "type": "text",
                    "text": result.stdout or "Files staged successfully",
                }
            ]
        }
    except Exception as e:
        return {
            "content": [{"type": "text", "text": f"Error: {e}"}],
            "is_error": True,
        }


@tool(
    "git_commit",
    "Create a git commit with the given message",
    {
        "message": str,
        "cwd": str | None,
    },
)
async def git_commit(args: dict[str, Any]) -> dict[str, Any]:
    """Create a git commit."""
    message = args["message"]
    cwd = args.get("cwd") or os.getcwd()

    try:
        result = subprocess.run(
            f'git commit -m "{message}"',
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )

        return {
            "content": [
                {
                    "type": "text",
                    "text": result.stdout or result.stderr,
                }
            ]
        }
    except Exception as e:
        return {
            "content": [{"type": "text", "text": f"Error: {e}"}],
            "is_error": True,
        }


@tool(
    "git_push",
    "Push commits to remote",
    {
        "remote": str | None,
        "branch": str | None,
        "cwd": str | None,
    },
)
async def git_push(args: dict[str, Any]) -> dict[str, Any]:
    """Push commits to remote repository."""
    remote = args.get("remote") or "origin"
    branch = args.get("branch")
    cwd = args.get("cwd") or os.getcwd()

    cmd = f"git push {remote}"
    if branch:
        cmd += f" {branch}"

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60,
            encoding="utf-8",
            errors="replace",
        )

        return {
            "content": [
                {
                    "type": "text",
                    "text": result.stdout or result.stderr,
                }
            ],
            "is_error": result.returncode != 0,
        }
    except Exception as e:
        return {
            "content": [{"type": "text", "text": f"Error: {e}"}],
            "is_error": True,
        }


# ============== Search Tools ==============

@tool(
    "grep",
    "Search for a pattern in files",
    {
        "pattern": str,
        "path": str | None,
        "file_glob": str | None,
        "case_sensitive": bool | None,
    },
)
async def grep(args: dict[str, Any]) -> dict[str, Any]:
    """Grep for a pattern in files."""
    pattern = args["pattern"]
    path = args.get("path") or "."
    file_glob = args.get("file_glob") or "*.cs"

    cmd = f'grep -r -n --include="{file_glob}" "{pattern}" {path}'
    if not args.get("case_sensitive", True):
        cmd = cmd.replace("grep", "grep -i")

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )

        # Limit output
        lines = (result.stdout or result.stderr).split("\n")
        output_lines = lines[:50]
        output = "\n".join(output_lines)
        if len(lines) > 50:
            output += f"\n... and {len(lines) - 50} more lines"

        return {
            "content": [
                {
                    "type": "text",
                    "text": output or "No matches found",
                }
            ]
        }
    except Exception as e:
        return {
            "content": [{"type": "text", "text": f"Error: {e}"}],
            "is_error": True,
        }


@tool(
    "list_files",
    "List files in a directory matching a glob pattern",
    {
        "pattern": str | None,
        "path": str | None,
    },
)
async def list_files(args: dict[str, Any]) -> dict[str, Any]:
    """List files matching a glob pattern."""
    pattern = args.get("pattern") or "**/*.cs"
    path = Path(args.get("path") or ".")

    try:
        files = list(path.glob(pattern))
        file_list = [str(f.relative_to(path)) for f in files[:100]]

        output = f"Found {len(files)} files:\n"
        output += "\n".join(file_list)

        if len(files) > 100:
            output += f"\n... and {len(files) - 100} more"

        return {
            "content": [
                {
                    "type": "text",
                    "text": output,
                }
            ]
        }
    except Exception as e:
        return {
            "content": [{"type": "text", "text": f"Error: {e}"}],
            "is_error": True,
        }


# ============== Utility Tools ==============

@tool(
    "get_file_outline",
    "Get the structure/outline of a C# file (namespaces, classes, methods)",
    {
        "file_path": str,
    },
)
async def get_file_outline(args: dict[str, Any]) -> dict[str, Any]:
    """Get the structure of a C# file."""
    file_path = Path(args["file_path"])

    if not file_path.exists():
        return {
            "content": [{"type": "text", "text": f"Error: File not found: {file_path}"}],
            "is_error": True,
        }

    try:
        content = file_path.read_text(encoding="utf-8")
        lines = content.split("\n")

        outline = []
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            # Simple heuristics for C# structure
            if stripped.startswith("namespace "):
                outline.append(f"{i}: NAMESPACE: {stripped.replace('namespace ', '')}")
            elif stripped.startswith("public class ") or stripped.startswith("private class "):
                outline.append(f"{i}: CLASS: {stripped}")
            elif stripped.startswith("public interface ") or stripped.startswith("private interface "):
                outline.append(f"{i}: INTERFACE: {stripped}")
            elif stripped.startswith("public enum ") or stripped.startswith("private enum "):
                outline.append(f"{i}: ENUM: {stripped}")
            elif "public " in stripped and "(" in stripped and "{" not in stripped:
                # Method signature
                method_name = stripped.split("(")[0].split()[-1]
                outline.append(f"{i}: METHOD: {method_name}")

        return {
            "content": [
                {
                    "type": "text",
                    "text": f"File: {file_path.name}\n\n" + "\n".join(outline) or "No structure found",
                }
            ]
        }
    except Exception as e:
        return {
            "content": [{"type": "text", "text": f"Error: {e}"}],
            "is_error": True,
        }


# ============== MCP Server Factory ==============

def create_sonar_mcp_server() -> Any:
    """Create the MCP server with all Sonar fix tools."""
    return create_sdk_mcp_server(
        name="sonar-fix",
        version="1.0.0",
        tools=[
            # File operations
            read_file,
            apply_edit,
            create_file,
            # Build
            run_build,
            run_tests,
            # Git
            git_status,
            git_add,
            git_commit,
            git_push,
            # Search
            grep,
            list_files,
            get_file_outline,
        ],
    )
