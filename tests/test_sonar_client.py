from __future__ import annotations

from pi_sonar_agent.integrations.sonar import extract_rule_detail_texts


def test_extract_rule_detail_texts_supports_legacy_md_fields() -> None:
    description, how_to_fix = extract_rule_detail_texts(
        {
            "mdDesc": "Old description",
            "mdNote": "Old fix note",
        }
    )

    assert description == "Old description"
    assert how_to_fix == "Old fix note"


def test_extract_rule_detail_texts_supports_description_sections() -> None:
    description, how_to_fix = extract_rule_detail_texts(
        {
            "descriptionSections": [
                {
                    "key": "introduction",
                    "content": "<p>This rule raises an issue when a method is too complex.</p>",
                },
                {
                    "key": "how_to_fix",
                    "content": (
                        "<p>Reduce complexity.</p>"
                        "<ul><li>Extract helpers</li><li>Flatten conditions</li></ul>"
                    ),
                },
                {
                    "key": "resources",
                    "content": "<p>Reference link</p>",
                },
            ]
        }
    )

    assert "This rule raises an issue when a method is too complex." in description
    assert "Reduce complexity." in how_to_fix
    assert "- Extract helpers" in how_to_fix
    assert "Reference link" not in description


def test_extract_rule_detail_texts_converts_html_links_to_readable_text() -> None:
    description, how_to_fix = extract_rule_detail_texts(
        {
            "descriptionSections": [
                {
                    "key": "introduction",
                    "content": '<p>Read <a href="https://example.com/doc">the guide</a>.</p>',
                },
                {
                    "key": "how_to_fix",
                    "content": '<p>Apply <a href="https://example.com/fix">this fix</a>.</p>',
                },
            ]
        }
    )

    assert "the guide (https://example.com/doc)" in description
    assert "this fix (https://example.com/fix)" in how_to_fix


def test_extract_rule_detail_texts_reuses_root_cause_when_fix_section_missing() -> None:
    description, how_to_fix = extract_rule_detail_texts(
        {
            "descriptionSections": [
                {
                    "key": "root_cause",
                    "content": "<p>Nested ternary expressions are difficult to read and maintain.</p>",
                },
            ]
        }
    )

    assert "Nested ternary expressions are difficult to read and maintain." in description
    assert how_to_fix == description


def test_extract_rule_detail_texts_supports_default_sections() -> None:
    description, how_to_fix = extract_rule_detail_texts(
        {
            "descriptionSections": [
                {
                    "key": "default",
                    "content": "<p>Publicly visible members should be documented with XML comments.</p>",
                },
            ]
        }
    )

    assert "Publicly visible members should be documented with XML comments." in description
    assert how_to_fix == description
