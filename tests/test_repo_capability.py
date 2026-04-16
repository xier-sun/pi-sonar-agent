from __future__ import annotations

import json

from pi_sonar_agent.core.repo_capability import detect_repo_capability


def test_detect_repo_capability_netcoreapp31_defaults_to_csharp8(tmp_path) -> None:
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

    profile = detect_repo_capability(tmp_path)

    assert profile.target_frameworks == ("netcoreapp3.1",)
    assert profile.lang_version == "default"
    assert profile.supports_record is False
    assert profile.supports_init_only is False
    assert "record" in profile.prompt_hints()[1]


def test_detect_repo_capability_lang_version_takes_precedence(tmp_path) -> None:
    project = tmp_path / "Foo.csproj"
    project.write_text(
        """
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net6.0</TargetFramework>
    <LangVersion>11</LangVersion>
    <Nullable>enable</Nullable>
  </PropertyGroup>
</Project>
""".strip(),
        encoding="utf-8",
    )

    profile = detect_repo_capability(tmp_path)

    assert profile.target_frameworks == ("net6.0",)
    assert profile.lang_version == "11"
    assert profile.nullable == "enable"
    assert profile.supports_record is True
    assert profile.supports_required is True
    assert json.loads(json.dumps(profile.to_dict()))["lang_version"] == "11"
