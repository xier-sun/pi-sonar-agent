from __future__ import annotations

from pi_sonar_agent.core.light_check_catalog import (
    load_default_light_check_catalog,
    load_default_sonar_fix_playbook,
    render_simple_loop_refactor_safety_constraints,
    render_simple_loop_guidance_for_rule,
)


def test_light_check_catalog_loads_high_frequency_rules() -> None:
    catalog = load_default_light_check_catalog()

    assert "csharpsquid:S3776" in catalog.rules
    assert "helper_type_shape_break" in catalog.blocker_categories


def test_render_simple_loop_guidance_merges_rule_and_family_items() -> None:
    guidance = render_simple_loop_guidance_for_rule("csharpsquid:S3776", max_items=4)

    assert guidance
    assert any("原方法体内收口复杂度" in item for item in guidance)
    assert any("private helper" in item or "private 且同步" in item for item in guidance)


def test_playbook_loads_common_refactor_safety_constraints() -> None:
    playbook = load_default_sonar_fix_playbook()

    assert playbook.common_guidance()
    assert playbook.refactor_safety_guidance()
    assert any("低风险重构" in item for item in playbook.common_guidance())
    assert any("匿名类型" in item and "显式参数" in item for item in playbook.refactor_safety_guidance())


def test_render_simple_loop_refactor_safety_constraints_returns_compact_constraints() -> None:
    constraints = render_simple_loop_refactor_safety_constraints("csharpsquid:S3776", max_items=5)

    assert constraints
    assert any("匿名类型" in item and "显式参数" in item for item in constraints)
    assert any("IQueryable" in item or "Expression<Func" in item for item in constraints)
