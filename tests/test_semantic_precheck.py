from __future__ import annotations

from pi_sonar_agent.core.diff_reviewer import ReviewedFileChange
from pi_sonar_agent.core.issue_contract import EditContract
from pi_sonar_agent.core.repo_capability import RepoCapabilityProfile
from pi_sonar_agent.core.semantic_precheck import SemanticPrecheck


def test_semantic_precheck_rejects_unsupported_record_syntax() -> None:
    result = SemanticPrecheck.review(
        issue_file_path="src/Foo.cs",
        edit_contract=EditContract(
            issue_key="ISSUE-SEMANTIC-RECORD",
            rule_id="csharpsquid:S107",
            guardrail_mode="contract_review",
            target_files=("src/Foo.cs",),
            repo_capability=RepoCapabilityProfile(
                target_frameworks=("netcoreapp3.1",),
                lang_version="default",
                nullable="disable",
                implicit_usings="disable",
                supports_record=False,
                supports_init_only=False,
                supports_required=False,
                supports_file_scoped_namespace=False,
                supports_global_using=False,
                evidence_files=("src/Foo.csproj",),
            ),
        ),
        reviewed_changes=(
            ReviewedFileChange(
                file="src/generated/FixArgs.cs",
                changed_lines=(1,),
                before_changed_lines=(),
                after_changed_lines=(1,),
                diff_text="@@ -0,0 +1,1 @@\n+public record FixArgs(string Name)\n",
                hunk_count=1,
                before_exists=False,
                after_exists=True,
            ),
        ),
        current_issue_file_content="class Foo {}\n",
    )

    assert result.status == "retry"
    assert any(item.finding_id == "language_feature_compatibility" for item in result.findings)


def test_semantic_precheck_rejects_anonymous_type_crossing_helper_boundary() -> None:
    result = SemanticPrecheck.review(
        issue_file_path="src/Foo.cs",
        edit_contract=EditContract(
            issue_key="ISSUE-SEMANTIC-ANON",
            rule_id="csharpsquid:S3776",
            guardrail_mode="contract_review",
            target_files=("src/Foo.cs",),
        ),
        reviewed_changes=(
            ReviewedFileChange(
                file="src/Foo.cs",
                changed_lines=(3, 4, 5, 6),
                before_changed_lines=(),
                after_changed_lines=(3, 4, 5, 6),
                diff_text=(
                    "@@ -3,0 +3,4 @@\n"
                    "+    private object BuildPayload()\n"
                    "+    {\n"
                    "+        return new { Name = \"demo\" };\n"
                    "+    }\n"
                ),
                hunk_count=1,
            ),
        ),
        current_issue_file_content="\n".join(
            [
                "class Foo",
                "{",
                "    private object BuildPayload()",
                "    {",
                "        return new { Name = \"demo\" };",
                "    }",
                "}",
            ]
        )
        + "\n",
    )

    assert result.status == "retry"
    assert any(item.finding_id == "anonymous_type_helper_boundary" for item in result.findings)


def test_semantic_precheck_allows_new_helpers_when_anonymous_type_stays_in_primary_method() -> None:
    current_content = "\n".join(
        [
            "class Foo",
            "{",
            "    private void Process()",
            "    {",
            "        var items = source",
            "            .Select(x => new",
            "            {",
            "                x.Id,",
            "                Score = ResolveScore(x)",
            "            })",
            "            .Where(x => x.Score > 0)",
            "            .ToList();",
            "    }",
            "",
            "    private static int ResolveScore(Item item)",
            "    {",
            "        return item.Score ?? 0;",
            "    }",
            "}",
        ]
    ) + "\n"

    result = SemanticPrecheck.review(
        issue_file_path="src/Foo.cs",
        edit_contract=EditContract(
            issue_key="ISSUE-SEMANTIC-ANON-PRIMARY",
            rule_id="csharpsquid:S3776",
            guardrail_mode="contract_review",
            target_files=("src/Foo.cs",),
        ),
        reviewed_changes=(
            ReviewedFileChange(
                file="src/Foo.cs",
                changed_lines=(5, 6, 7, 8, 9, 10, 15, 16, 17),
                before_changed_lines=(),
                after_changed_lines=(5, 6, 7, 8, 9, 10, 15, 16, 17),
                diff_text=(
                    "@@ -5,3 +5,8 @@\n"
                    "+        var items = source\n"
                    "+            .Select(x => new\n"
                    "+            {\n"
                    "+                x.Id,\n"
                    "+                Score = ResolveScore(x)\n"
                    "+            })\n"
                    "+            .Where(x => x.Score > 0)\n"
                    "+            .ToList();\n"
                    "@@ -0,0 +15,3 @@\n"
                    "+    private static int ResolveScore(Item item)\n"
                    "+    {\n"
                    "+        return item.Score ?? 0;\n"
                    "+    }\n"
                ),
                hunk_count=2,
            ),
        ),
        current_issue_file_content=current_content,
    )

    assert result.status == "pass"
    assert not any(item.finding_id == "anonymous_type_helper_boundary" for item in result.findings)


def test_semantic_precheck_rejects_dynamic_helper_signatures_for_s3776() -> None:
    current_content = "\n".join(
        [
            "class Foo",
            "{",
            "    private void Process()",
            "    {",
            "        if (true) { }",
            "    }",
            "",
            "    private void ProcessOrderGroupForPenalty(",
            "        IGrouping<int, dynamic> group,",
            "        ILookup<int, dynamic> items)",
            "    {",
            "    }",
            "}",
        ]
    ) + "\n"

    result = SemanticPrecheck.review(
        issue_file_path="src/Foo.cs",
        edit_contract=EditContract(
            issue_key="ISSUE-SEMANTIC-DYNAMIC-HELPER",
            rule_id="csharpsquid:S3776",
            guardrail_mode="contract_review",
            target_files=("src/Foo.cs",),
        ),
        reviewed_changes=(
            ReviewedFileChange(
                file="src/Foo.cs",
                changed_lines=(8, 9, 10, 11, 12),
                before_changed_lines=(),
                after_changed_lines=(8, 9, 10, 11, 12),
                diff_text=(
                    "@@ -0,0 +8,5 @@\n"
                    "+    private void ProcessOrderGroupForPenalty(\n"
                    "+        IGrouping<int, dynamic> group,\n"
                    "+        ILookup<int, dynamic> items)\n"
                    "+    {\n"
                    "+    }\n"
                ),
                hunk_count=1,
            ),
        ),
        current_issue_file_content=current_content,
    )

    assert result.status == "retry"
    assert any(item.finding_id == "dynamic_helper_signature_boundary" for item in result.findings)


def test_semantic_precheck_rejects_new_type_when_repair_plan_forbids_it() -> None:
    repair_plan = type(
        "RepairPlanStub",
        (),
        {
            "requires_new_type": False,
            "requires_signature_change": False,
            "proposed_method_name": "",
            "propagation_targets": (),
        },
    )()
    result = SemanticPrecheck.review(
        issue_file_path="src/Foo.cs",
        edit_contract=EditContract(
            issue_key="ISSUE-PLAN-NEW-TYPE",
            rule_id="csharpsquid:S3776",
            guardrail_mode="contract_review",
            target_files=("src/Foo.cs",),
            repair_plan=repair_plan,
        ),
        reviewed_changes=(
            ReviewedFileChange(
                file="src/Foo.cs",
                changed_lines=(8,),
                before_changed_lines=(),
                after_changed_lines=(8,),
                diff_text="@@ -0,0 +8,1 @@\n+internal class ExtractedState\n",
                hunk_count=1,
            ),
        ),
        current_issue_file_content="class Foo {}\n",
    )

    assert result.status == "retry"
    assert any(item.finding_id == "repair_plan_new_type_forbidden" for item in result.findings)


def test_semantic_precheck_rejects_non_private_signature_change_when_plan_forbids_it() -> None:
    repair_plan = type(
        "RepairPlanStub",
        (),
        {
            "requires_new_type": False,
            "requires_signature_change": False,
            "proposed_method_name": "",
            "propagation_targets": (),
        },
    )()
    result = SemanticPrecheck.review(
        issue_file_path="src/Foo.cs",
        edit_contract=EditContract(
            issue_key="ISSUE-PLAN-SIGNATURE",
            rule_id="csharpsquid:S3776",
            guardrail_mode="contract_review",
            target_files=("src/Foo.cs",),
            repair_plan=repair_plan,
        ),
        reviewed_changes=(
            ReviewedFileChange(
                file="src/Foo.cs",
                changed_lines=(5,),
                before_changed_lines=(),
                after_changed_lines=(5,),
                diff_text="@@ -0,0 +5,1 @@\n+public Task HandleAsync(string input)\n",
                hunk_count=1,
            ),
        ),
        current_issue_file_content="class Foo {}\n",
    )

    assert result.status == "retry"
    assert any(
        item.finding_id == "repair_plan_signature_change_forbidden" for item in result.findings
    )
