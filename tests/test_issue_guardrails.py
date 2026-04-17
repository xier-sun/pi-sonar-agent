from __future__ import annotations

import json
from pathlib import Path

from pi_sonar_agent.agent.claude_agent import ClaudeFixAgent, SonarIssue
from pi_sonar_agent.core.diff_reviewer import DiffReviewer, FollowUpItem, ReviewedFileChange
from pi_sonar_agent.core.diff_reviewer import ReviewedLineOperation
from pi_sonar_agent.core.follow_up_store import FollowUpStore
from pi_sonar_agent.core.issue_contract import EditContract
from pi_sonar_agent.core.issue_planner import IssuePlanner
from pi_sonar_agent.core.issue_retry import build_retry_feedback
from pi_sonar_agent.core.editor_policy import EditorPolicy


def test_issue_planner_builds_edit_contract_for_contract_review() -> None:
    plan = IssuePlanner.plan_issue(
        issue_key="ISSUE-1",
        rule_id="csharpsquid:S3358",
        file_path="/src/Foo.cs",
        issue_line=18,
        guardrail_mode="contract_review",
        scope_mode="expression_rewrite",
        scope_start_line=15,
        scope_end_line=24,
        validation_start_line=13,
        validation_end_line=30,
        workspace_rules="keep patches small",
    )

    assert plan.strategy.startswith("apply the smallest issue-focused patch")
    assert plan.edit_contract.guardrail_mode == "contract_review"
    assert plan.edit_contract.target_files == ("src/Foo.cs",)
    assert plan.edit_contract.patch_only is True
    assert "lambda-rewrite" in plan.edit_contract.allowed_change_kinds
    assert plan.edit_contract.quality_gate_rules
    assert any(rule.rule_id == "public_xml_docs" for rule in plan.edit_contract.quality_gate_rules)
    assert "Edit Contract" in plan.prompt_guidance
    assert "Hard Quality Gates" in plan.prompt_guidance
    assert "Quality Gate Notes" in plan.prompt_guidance


def test_issue_planner_disables_file_creation_in_simple_loop() -> None:
    plan = IssuePlanner.plan_issue(
        issue_key="ISSUE-S107",
        rule_id="csharpsquid:S107",
        file_path="/src/Foo.cs",
        issue_line=18,
        guardrail_mode="contract_review",
        scope_mode="statement",
        scope_start_line=15,
        scope_end_line=24,
        validation_start_line=13,
        validation_end_line=30,
        workspace_rules="keep patches small",
    )

    assert plan.edit_contract.allow_file_creation is False
    assert plan.edit_contract.allowed_new_file_roots == ()


def test_editor_policy_keeps_write_for_existing_files_when_file_creation_is_disabled() -> None:
    contract = EditContract(
        issue_key="ISSUE-CREATEFILE",
        rule_id="csharpsquid:S107",
        guardrail_mode="contract_review",
        target_files=("src/Foo.cs",),
        allow_file_creation=False,
        allowed_new_file_roots=(),
        patch_only=True,
    )

    allowed_tools = EditorPolicy.allowed_tool_names(("Read", "Edit", "Write"), contract)
    constraints = EditorPolicy.render_prompt_constraints(contract)

    assert "Write" in allowed_tools
    assert "新建文件优先使用 Write" not in constraints
    assert "Write 只能用于重写已有文件，不能借此创建新文件" in constraints


def test_issue_planner_includes_repo_capability_guidance(tmp_path: Path) -> None:
    project = tmp_path / "src" / "Foo.csproj"
    project.parent.mkdir(parents=True, exist_ok=True)
    project.write_text(
        """
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>netcoreapp3.1</TargetFramework>
  </PropertyGroup>
</Project>
""".strip(),
        encoding="utf-8",
    )
    source_file = tmp_path / "src" / "Foo.cs"
    source_file.write_text("class Foo {}\n", encoding="utf-8")

    plan = IssuePlanner.plan_issue(
        issue_key="ISSUE-CAP",
        rule_id="csharpsquid:S3776",
        file_path="/src/Foo.cs",
        issue_line=3,
        guardrail_mode="contract_review",
        scope_mode="statement",
        scope_start_line=1,
        scope_end_line=3,
        validation_start_line=1,
        validation_end_line=5,
        workspace_rules="keep patches small",
        workspace_path=tmp_path,
    )

    assert "Repo Capability: TFM=netcoreapp3.1; LangVersion=default" in plan.prompt_guidance
    assert plan.edit_contract.repo_capability is not None
    assert any("record" in item for item in plan.edit_contract.repo_capability_hints)


def test_diff_reviewer_allows_existing_file_modification_without_scope_audit() -> None:
    contract = EditContract(
        issue_key="ISSUE-2",
        rule_id="csharpsquid:S6562",
        guardrail_mode="contract_review",
        target_files=("src/Foo.cs",),
        validation_plan=("build", "diff_review"),
        scope_mode="statement",
        validation_line_range=(7, 7),
    )
    result = DiffReviewer.review(
        edit_contract=contract,
        file_changes=(
            ReviewedFileChange(
                file="src/Bar.cs",
                changed_lines=(4, 5),
                diff_text="@@ -4,0 +4,2 @@\n+bar\n+baz",
                hunk_count=1,
            ),
        ),
    )

    assert result.status == "pass"
    assert result.violations == ()
    assert result.follow_ups == ()
    assert result.metrics["drift_score"] == 0


def test_diff_reviewer_rejects_created_file_even_if_root_was_declared() -> None:
    contract = EditContract(
        issue_key="ISSUE-CREATE",
        rule_id="csharpsquid:S107",
        guardrail_mode="contract_review",
        target_files=("src/Foo.cs",),
        validation_plan=("build", "diff_review"),
        scope_mode="statement",
        validation_line_range=(7, 7),
        allow_file_creation=True,
        allowed_new_file_roots=("src/generated",),
    )
    result = DiffReviewer.review(
        edit_contract=contract,
        file_changes=(
            ReviewedFileChange(
                file="src/generated/NewType.cs",
                changed_lines=(1, 2),
                diff_text="@@ -0,0 +1,2 @@\n+class NewType {}\n+",
                hunk_count=1,
                before_exists=False,
                after_exists=True,
                after_changed_lines=(1, 2),
            ),
        ),
    )

    assert result.status == "retry"
    assert any(item.type == "file_created" for item in result.violations)


def test_diff_reviewer_allows_private_helper_addition() -> None:
    contract = EditContract(
        issue_key="ISSUE-NO-HELPER",
        rule_id="csharpsquid:S3776",
        guardrail_mode="scope",
        target_files=("src/Foo.cs",),
        validation_plan=("build", "diff_review"),
        scope_mode="method",
    )
    result = DiffReviewer.review(
        edit_contract=contract,
        file_changes=(
            ReviewedFileChange(
                file="src/Foo.cs",
                changed_lines=(18,),
                before_changed_lines=(18,),
                after_changed_lines=(18,),
                diff_text="@@ -17,0 +18,4 @@\n+    private decimal ComputeReturnedQty(\n+        OrderItem item)\n+    {\n+    }\n",
                hunk_count=1,
                line_operations=(
                    ReviewedLineOperation(
                        kind="add",
                        after_line=18,
                        text="    private decimal ComputeReturnedQty(",
                    ),
                ),
            ),
        ),
    )

    assert result.status == "pass"
    assert result.violations == ()


def test_follow_up_store_appends_jsonl(tmp_path: Path) -> None:
    store = FollowUpStore(root=tmp_path / "follow-ups")
    queue_path = store.append(
        repository="repo",
        run_label="run1",
        issue_key="ISSUE-3",
        follow_ups=(
            FollowUpItem(
                source_issue_key="ISSUE-3",
                file="src/Foo.cs",
                symbol="statement@7-7",
                summary="Potential adjacent cleanup",
                evidence_hunk="@@ -7,1 +7,1 @@",
                discovered_at="2026-04-03T10:00:00+00:00",
            ),
        ),
    )

    assert queue_path is not None
    payload = json.loads(queue_path.read_text(encoding="utf-8").splitlines()[0])
    assert payload["source_issue_key"] == "ISSUE-3"
    assert payload["summary"] == "Potential adjacent cleanup"


def test_build_retry_feedback_includes_reviewer_rejection(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    result = type(
        "Result",
        (),
        {
            "build_output": "Diff reviewer rejected the patch because it no longer stays inside the issue contract.",
            "failure_kind": "reviewer",
            "error": "Diff reviewer rejected the patch",
            "summary": "",
            "build_command": "dotnet build",
            "retryable_failure": True,
            "build_verification_failed": False,
            "changes": [{"file": "src/Foo.cs", "action": "modified"}],
            "reviewer_result": {
                "status": "retry",
                "summary": "Patch touches undeclared files or lines outside the declared edit contract.",
                "violations": [
                    {
                        "type": "incidental_fix",
                        "file": "src/Foo.cs",
                        "reason": "This hunk changes lines outside the edit contract window 7-7.",
                        "changed_lines": [10],
                    }
                ],
            },
        },
    )()

    feedback = build_retry_feedback(repo, result)

    assert "变更审查拒绝" in feedback
    assert "Edit Contract 之外的文件或无关代码行" in feedback


def test_fix_issue_contract_review_allows_same_file_drift_and_records_audit(monkeypatch, tmp_path: Path) -> None:
    agent = ClaudeFixAgent(
        sonar_host="https://sonar.example",
        sonar_token="token",
        agent_env={"ISSUE_GUARDRAIL_MODE": "contract_review"},
    )
    issue = SonarIssue(
        key="issue-contract-review",
        rule="csharpsquid:S6562",
        message="DateTime 应显式指定 Kind",
        line=5,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    source_file = tmp_path / "src" / "Foo.cs"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text(
        "\n".join(
            [
                "class Foo",
                "{",
                "    void Demo()",
                "    {",
                "        var now = DateTime.Now;",
                "        var name = \"demo\";",
                "    }",
                "}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        ClaudeFixAgent,
        "get_rule_details",
        lambda self, rule_key: {"description": "原因", "how_to_fix": "修复方法"},
    )
    monkeypatch.setattr(
        ClaudeFixAgent,
        "_collect_modified_files",
        staticmethod(lambda workspace_path: ["src/Foo.cs"]),
    )

    import pi_sonar_agent.agent.claude_agent as claude_agent_module

    def fake_fix_run(*args, **kwargs):
        source_file.write_text(
            "\n".join(
                [
                    "class Foo",
                    "{",
                    "    void Demo()",
                    "    {",
                    "        var now = DateTime.SpecifyKind(DateTime.Now, DateTimeKind.Local);",
                    "        var name = \"changed outside contract\";",
                    "    }",
                    "}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return claude_agent_module.AgentRuntimeResult()

    monkeypatch.setattr(agent, "_run_runtime_with_continuation", fake_fix_run)

    monkeypatch.setattr(
        ClaudeFixAgent,
        "_run_prompt_only_role_session",
        classmethod(
            lambda cls, **kwargs: claude_agent_module.RoleAgentRunResult(
                role=str(kwargs.get("role", "")),
                response_text=(
                    '{"decision":"approve","summary":"代码符合门禁，可以进入编译。","findings":[],"constraints":[]}'
                    if kwargs.get("role") == "review"
                    else '{"decision":"compile","summary":"进入编译阶段。","constraints":[]}'
                ),
            )
        ),
    )

    class FakeCompletedProcess:
        returncode = 0
        stdout = "build ok"
        stderr = ""

    monkeypatch.setattr(
        claude_agent_module.subprocess,
        "run",
        lambda *args, **kwargs: FakeCompletedProcess(),
    )

    result = agent.fix_issue(issue, tmp_path, 'dotnet build "src/Foo.sln"')

    assert result.success is True
    assert result.retryable_failure is False
    assert result.guardrail_mode == "contract_review"
    assert result.reviewer_result["status"] == "pass"
    assert result.reviewer_result["violations"] == []
    assert result.follow_ups == ()
