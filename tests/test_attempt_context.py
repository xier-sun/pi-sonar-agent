from __future__ import annotations

from pi_sonar_agent.core.attempt_context import AttemptContextCache


def test_attempt_context_cache_reuses_and_invalidates_file_content(tmp_path) -> None:
    path = tmp_path / "Foo.cs"
    path.write_text("class Foo {}\n", encoding="utf-8")

    cache = AttemptContextCache()
    first_text = cache.read_text(path)
    first_window = cache.render_numbered_window(path, 1, 1)

    path.write_text("class Foo { int Value => 1; }\n", encoding="utf-8")
    cache.invalidate(path)

    second_text = cache.read_text(path)
    second_window = cache.render_numbered_window(path, 1, 1)

    assert first_text == "class Foo {}\n"
    assert "class Foo {}" in first_window
    assert second_text == "class Foo { int Value => 1; }\n"
    assert "Value => 1" in second_window
