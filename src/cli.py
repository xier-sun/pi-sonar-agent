"""CLI entry point for pi-sonar-agent."""

import os
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from pi_sonar_agent.agent.claude_agent import ClaudeFixAgent
from pi_sonar_agent.core.model_env import (
    build_agent_env,
    load_project_env,
    resolve_agent_model,
    validate_agent_env,
)
from pi_sonar_agent.fixers.build_gate import resolve_build_command

# Load environment variables
load_project_env()

app = typer.Typer(
    name="pi-sonar-agent",
    help="SonarQube Code Issue Fix Agent based on Claude Code SDK",
)
console = Console()


def require_env(name: str, default: str | None = None) -> str:
    """Get required environment variable."""
    value = os.getenv(name, default)
    if value is None or value.strip() == "":
        console.print(f"[red]Error:[/red] Missing environment variable: {name}")
        sys.exit(1)
    return value.strip()


@app.command()
def fix(
    project_key: str = typer.Option(..., "--project-key", "-p", help="SonarQube project key"),
    author: str = typer.Option(..., "--author", "-a", help="Author to filter issues"),
    repository: str = typer.Option(..., "--repository", "-r", help="Azure DevOps repository"),
    max_issues: int = typer.Option(0, "--max-issues", "-m", help="Max issues to fix (0=all)"),
    base_branch: str = typer.Option("develop", "--base-branch", "-b", help="Base branch for PR"),
    build_command: str = typer.Option("dotnet build", "--build-command", help="Build command"),
    solution_path: str = typer.Option("", "--solution-path", "-s", help=".sln or .csproj path"),
    keep_workspace: bool = typer.Option(False, "--keep-workspace", help="Keep workspace after run"),
    skip_build: bool = typer.Option(False, "--skip-build", help="Skip build verification"),
) -> None:
    """Fix SonarQube code issues using Claude Code."""
    console.print("[bold blue]SonarQube Fix Agent[/bold blue]")
    console.print(f"Project: {project_key}, Author: {author}")
    console.print()

    # Get configuration
    sonar_host = require_env("SONARQUBE_HOST", "http://localhost:9000")
    sonar_token = require_env("SONARQUBE_TOKEN")
    sonar_org = os.getenv("SONARQUBE_ORG")
    workspace_root = os.getenv("WORKSPACE_ROOT", ".agent_workspaces")
    model_env_errors = validate_agent_env()
    if model_env_errors:
        console.print("[red]Error:[/red] Invalid model configuration:")
        for error in model_env_errors:
            console.print(f"  - {error}")
        raise typer.Exit(code=1)

    # Create agent
    agent = ClaudeFixAgent(
        sonar_host=sonar_host,
        sonar_token=sonar_token,
        sonar_org=sonar_org,
        workspace_root=workspace_root,
        agent_env=build_agent_env(),
        model=resolve_agent_model(),
    )

    # Get issues
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Fetching SonarQube issues...", total=None)
        issues = agent.get_issues(project_key=project_key, author=author)
        progress.update(task, completed=True)

    console.print(f"[green]Found {len(issues)} issues[/green]")

    if max_issues > 0:
        issues = issues[:max_issues]
        console.print(f"[yellow]Limited to {max_issues} issues[/yellow]")

    # Sort by severity
    severity_order = {"BLOCKER": 0, "CRITICAL": 1, "MAJOR": 2, "MINOR": 3, "INFO": 4}
    issues.sort(key=lambda x: severity_order.get(x.severity, 5))

    # Process each issue
    results = []
    for i, issue in enumerate(issues, 1):
        console.print(f"\n[{i}/{len(issues)}] Processing: {issue.rule}")
        console.print(f"  File: {issue.file_path}:{issue.line}")
        console.print(f"  Message: {issue.message[:100]}...")

        # Create workspace for this issue
        workspace = Path(workspace_root) / issue.key
        workspace.mkdir(parents=True, exist_ok=True)

        # Note: In production, would clone repo to workspace first
        # For now, we just demonstrate the agent setup

        result = agent.fix_issue(issue, workspace, resolve_build_command(build_command, solution_path))
        results.append(result)

        if result.success:
            console.print("  [green]✓ Fixed[/green]")
        else:
            console.print(f"  [red]✗ Failed: {result.error}[/red]")

    # Summary
    success_count = sum(1 for r in results if r.success)
    console.print("\n[bold]Summary:[/bold]")
    console.print(f"  Total: {len(results)}")
    console.print(f"  Successful: {success_count}")
    console.print(f"  Failed: {len(results) - success_count}")


@app.command()
def list_issues(
    project_key: str = typer.Option(..., "--project-key", "-p", help="SonarQube project key"),
    author: str | None = typer.Option(None, "--author", "-a", help="Filter by author"),
    severity: str | None = typer.Option(None, "--severity", "-s", help="Filter by severity"),
    limit: int = typer.Option(50, "--limit", "-l", help="Max issues to show"),
) -> None:
    """List SonarQube issues."""
    sonar_host = require_env("SONARQUBE_HOST", "http://localhost:9000")
    sonar_token = require_env("SONARQUBE_TOKEN")
    sonar_org = os.getenv("SONARQUBE_ORG")
    model_env_errors = validate_agent_env()
    if model_env_errors:
        console.print("[red]Error:[/red] Invalid model configuration:")
        for error in model_env_errors:
            console.print(f"  - {error}")
        raise typer.Exit(code=1)

    agent = ClaudeFixAgent(
        sonar_host=sonar_host,
        sonar_token=sonar_token,
        sonar_org=sonar_org,
        agent_env=build_agent_env(),
        model=resolve_agent_model(),
    )

    severities = [severity.upper()] if severity else None
    issues = agent.get_issues(project_key=project_key, author=author, severities=severities)

    console.print(f"[green]Found {len(issues)} issues[/green]\n")

    for issue in issues[:limit]:
        severity_emoji = {
            "BLOCKER": "🔴",
            "CRITICAL": "🟠",
            "MAJOR": "🟡",
            "MINOR": "🔵",
            "INFO": "⚪",
        }.get(issue.severity, "⚪")

        console.print(f"{severity_emoji} [{issue.severity}] {issue.rule}")
        console.print(f"  {issue.file_path}:{issue.line}")
        console.print(f"  {issue.message[:80]}...")
        console.print()


@app.command()
def init() -> None:
    """Initialize configuration files."""
    env_file = Path(".env")
    example_file = Path(__file__).parent.parent / ".env.example"

    if env_file.exists():
        console.print("[yellow].env already exists[/yellow]")
        return

    if example_file.exists():
        import shutil

        shutil.copy(example_file, env_file)
        console.print("[green]Created .env from template[/green]")
        console.print("[yellow]Please edit .env with your credentials[/yellow]")
    else:
        console.print("[red].env.example not found[/yellow]")


def main() -> None:
    """Main entry point."""
    app()


if __name__ == "__main__":
    main()
