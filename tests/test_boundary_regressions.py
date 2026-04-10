from __future__ import annotations

from pi_sonar_agent.core.issue_planner import IssuePlanner


def _build_sparse_lines(line_map: dict[int, str], total_lines: int) -> tuple[str, ...]:
    lines = ["" for _ in range(total_lines)]
    for line_number, text in line_map.items():
        lines[line_number - 1] = text
    return tuple(lines)


def test_bi_s1481_regression_contract_includes_declaration_anchor() -> None:
    source_lines = _build_sparse_lines(
        {
            2221: "            var result = new List<string>();",
            2223: "            var slpDict = await UnitWork.Find<OSLP>(null).ToDictionaryAsync(x => x.SlpCode, x => x.SlpName);",
            2224: "            return result;",
            2225: "        }",
        },
        total_lines=2230,
    )

    plan = IssuePlanner.plan_issue(
        issue_key="BI-S1481",
        rule_id="csharpsquid:S1481",
        file_path="OpenAuth.Core/OpenAuth.App/Finance/FinanceHomeApp.cs",
        issue_line=2224,
        guardrail_mode="contract_review",
        scope_mode="statement",
        scope_start_line=2224,
        scope_end_line=2224,
        validation_start_line=2224,
        validation_end_line=2225,
        source_lines=source_lines,
    )

    assert any(start_line <= 2223 <= end_line for start_line, end_line in plan.edit_contract.allowed_line_ranges)
    assert any(
        symbol.symbol.startswith("declaration_anchor@2223-2223")
        for symbol in plan.edit_contract.allowed_related_symbols
    )


def test_bi_s125_regression_contract_includes_adjacent_cleanup_anchor() -> None:
    source_lines = _build_sparse_lines(
        {
            2223: "            var slpDict = await UnitWork.Find<OSLP>(null).ToDictionaryAsync(x => x.SlpCode, x => x.SlpName);",
            2227: "            //await AddReceiptsAsync(result, req, slpDict);",
            2228: "            return result;",
            2231: "        }",
        },
        total_lines=2235,
    )

    plan = IssuePlanner.plan_issue(
        issue_key="BI-S125",
        rule_id="csharpsquid:S125",
        file_path="OpenAuth.Core/OpenAuth.App/Finance/FinanceHomeApp.cs",
        issue_line=2228,
        guardrail_mode="contract_review",
        scope_mode="statement",
        scope_start_line=2228,
        scope_end_line=2228,
        validation_start_line=2224,
        validation_end_line=2231,
        source_lines=source_lines,
    )

    assert any(start_line <= 2223 <= end_line for start_line, end_line in plan.edit_contract.allowed_line_ranges)
    assert any(
        symbol.symbol.startswith("adjacent_cleanup@2223-2223")
        for symbol in plan.edit_contract.allowed_related_symbols
    )


def test_bi_s1144_regression_contract_includes_member_cluster_range() -> None:
    source_lines = _build_sparse_lines(
        {
            2390: "        private async Task AddReceiptsAsync(List<AgingRawItem> result, OverdueAgingChartReq req, Dictionary<int, string> slpDict)",
            2391: "        {",
            2412: "        }",
            2414: "        private async Task AddReceiptsCaseAAsync(List<AgingRawItem> result, IQueryable<ORCT> baseQuery, Dictionary<int, string> slpDict, OverdueAgingChartReq req)",
            2415: "        {",
            2438: "        }",
            2440: "        public async Task KeepMeAsync()",
            2441: "        {",
            2443: "        }",
        },
        total_lines=2450,
    )

    plan = IssuePlanner.plan_issue(
        issue_key="BI-S1144",
        rule_id="csharpsquid:S1144",
        file_path="OpenAuth.Core/OpenAuth.App/Finance/FinanceHomeApp.cs",
        issue_line=2394,
        guardrail_mode="contract_review",
        scope_mode="method",
        scope_start_line=2390,
        scope_end_line=2412,
        validation_start_line=2390,
        validation_end_line=2412,
        source_lines=source_lines,
    )

    assert (2414, 2438) in plan.edit_contract.allowed_line_ranges
    assert any(
        symbol.symbol.startswith("method_cluster@2414-2438")
        for symbol in plan.edit_contract.allowed_related_symbols
    )
