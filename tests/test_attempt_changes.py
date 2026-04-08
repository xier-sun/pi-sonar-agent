from __future__ import annotations

import subprocess
from pathlib import Path

from pi_sonar_agent.core.attempt_changes import AttemptFileChangeBuilder


def test_extract_touched_line_facts_keep_delete_coordinates_in_before_space() -> None:
    diff_text = "\n".join(
        [
            "--- a/src/Foo.cs",
            "+++ b/src/Foo.cs",
            "@@ -4,5 +4,4 @@",
            "     void Demo()",
            "     {",
            "-        var temp = 1;",
            "         Run();",
            "-        //old code",
            "     }",
        ]
    )

    facts = AttemptFileChangeBuilder.extract_touched_line_facts(diff_text)

    assert facts.before_changed_lines == (6, 8)
    assert facts.after_changed_lines == ()
    assert tuple(operation.kind for operation in facts.line_operations) == ("delete", "delete")
    assert AttemptFileChangeBuilder.extract_touched_line_numbers(diff_text) == {6, 8}


def test_extract_touched_line_facts_preserves_old_line_for_single_delete_hunk() -> None:
    diff_text = "\n".join(
        [
            "--- a/src/Foo.cs",
            "+++ b/src/Foo.cs",
            "@@ -2224 +2223,0 @@",
            "-        var orderNum = result.OrderNum;",
        ]
    )

    facts = AttemptFileChangeBuilder.extract_touched_line_facts(diff_text)

    assert facts.before_changed_lines == (2224,)
    assert facts.after_changed_lines == ()
    assert facts.line_operations[0].before_line == 2224
    assert facts.line_operations[0].after_line == 2223


def test_build_uses_head_commit_for_clean_tracked_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "bot@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "bot"], cwd=repo, check=True)

    source_file = repo / "src" / "Foo.cs"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("class Foo\n{\n    void Demo() { }\n}\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True, text=True)
    head_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    (repo / ".git" / "pi-sonar-agent-attempt-state" / "files").mkdir(parents=True, exist_ok=True)
    source_file.write_text("class Foo\n{\n    void Demo() { /*changed*/ }\n}\n", encoding="utf-8")

    changes = AttemptFileChangeBuilder.build(
        workspace_path=repo,
        changed_files=("src/Foo.cs",),
        manifest={"head_commit": head_commit, "status_paths": [], "existing_paths": []},
    )

    assert len(changes) == 1
    assert changes[0].before_exists is True
    assert changes[0].changed_lines == (3,)
    assert changes[0].before_changed_lines == (3,)
    assert changes[0].after_changed_lines == (3,)
    assert "@@ -3 +3 @@" in changes[0].diff_text
