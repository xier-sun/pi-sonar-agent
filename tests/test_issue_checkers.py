from __future__ import annotations

from types import SimpleNamespace

from pi_sonar_agent.agent.claude_agent import SonarIssue
from pi_sonar_agent.core.issue_checkers import run_issue_check
from pi_sonar_agent.core.quality_gate_verifier import QualityGateVerifier


def test_issue_checker_s107_passes_when_parameter_count_is_within_threshold() -> None:
    issue = SonarIssue(
        key="issue-s107-pass",
        rule="csharpsquid:S107",
        message="方法参数过多",
        line=2,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    content = """
class Foo
{
    private void Process(int a, int b, int c, int d, int e, int f, int g)
    {
    }
}
"""

    result = run_issue_check(issue=issue, file_content=content)

    assert result.status == "PASS"


def test_issue_checker_s107_fails_when_parameter_count_still_exceeds_threshold() -> None:
    issue = SonarIssue(
        key="issue-s107-fail",
        rule="csharpsquid:S107",
        message="方法参数过多",
        line=2,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    content = """
class Foo
{
    private void Process(int a, int b, int c, int d, int e, int f, int g, int h)
    {
    }
}
"""

    result = run_issue_check(issue=issue, file_content=content)

    assert result.status == "FAIL"


def test_issue_checker_s1172_detects_unused_parameter() -> None:
    issue = SonarIssue(
        key="issue-s1172-fail",
        rule="csharpsquid:S1172",
        message="Remove this unused method parameter 'unused'.",
        line=2,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    content = """
class Foo
{
    private void Process(int used, int unused)
    {
        var value = used + 1;
    }
}
"""

    result = run_issue_check(issue=issue, file_content=content)

    assert result.status == "FAIL"


def test_issue_checker_s1172_passes_when_parameter_is_used() -> None:
    issue = SonarIssue(
        key="issue-s1172-pass",
        rule="csharpsquid:S1172",
        message="Remove this unused method parameter 'unused'.",
        line=2,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    content = """
class Foo
{
    private void Process(int used, int unused)
    {
        var value = used + unused;
    }
}
"""

    result = run_issue_check(issue=issue, file_content=content)

    assert result.status == "PASS"


def test_issue_checker_s1481_detects_unused_local_variable() -> None:
    issue = SonarIssue(
        key="issue-s1481-fail",
        rule="csharpsquid:S1481",
        message="Remove this unused local variable 'temp'.",
        line=4,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    content = """
class Foo
{
    private void Process()
    {
        var temp = 1;
    }
}
"""

    result = run_issue_check(issue=issue, file_content=content)

    assert result.status == "FAIL"


def test_issue_checker_s1144_detects_remaining_private_member() -> None:
    issue = SonarIssue(
        key="issue-s1144-fail",
        rule="csharpsquid:S1144",
        message="Remove this unused private method 'Cleanup'.",
        line=2,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    content = """
class Foo
{
    private void Cleanup()
    {
    }
}
"""

    result = run_issue_check(issue=issue, file_content=content)

    assert result.status == "FAIL"


def test_issue_checker_s3776_passes_for_low_complexity_method() -> None:
    issue = SonarIssue(
        key="issue-s3776-pass",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=4,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    content = """
class Foo
{
    private void Process(int value)
    {
        if (value > 0)
        {
            return;
        }
    }
}
"""

    result = run_issue_check(issue=issue, file_content=content)

    assert result.status == "PASS"


def test_issue_checker_s3776_treats_equal_fail_threshold_as_unknown(monkeypatch) -> None:
    issue = SonarIssue(
        key="issue-s3776-boundary-unknown",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=4,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    monkeypatch.setattr(
        QualityGateVerifier,
        "_find_enclosing_method",
        lambda lines, target_line: SimpleNamespace(
            start_line=1,
            end_line=3,
            name="Process",
            signature="private void Process()",
        ),
    )
    monkeypatch.setattr(QualityGateVerifier, "_estimate_cognitive_complexity", lambda body_text: 30)

    result = run_issue_check(issue=issue, file_content="class Foo { }")

    assert result.status == "UNKNOWN"
    assert "above the allowed threshold" not in result.summary


def test_issue_checker_s3776_returns_unknown_above_fail_threshold(monkeypatch) -> None:
    issue = SonarIssue(
        key="issue-s3776-boundary-fail",
        rule="csharpsquid:S3776",
        message="认知复杂度过高",
        line=4,
        component="BI:src/Foo.cs",
        severity="MAJOR",
        issue_type="CODE_SMELL",
    )

    monkeypatch.setattr(
        QualityGateVerifier,
        "_find_enclosing_method",
        lambda lines, target_line: SimpleNamespace(
            start_line=1,
            end_line=3,
            name="Process",
            signature="private void Process()",
        ),
    )
    monkeypatch.setattr(QualityGateVerifier, "_estimate_cognitive_complexity", lambda body_text: 31)

    result = run_issue_check(issue=issue, file_content="class Foo { }")

    assert result.status == "UNKNOWN"
    assert "heuristic-only" in result.summary
