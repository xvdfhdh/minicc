"""测试技能系统。"""
from __future__ import annotations
from pathlib import Path
from src.prompt.skills.skills import (
    resolve_skill_prompt,
    build_skill_description,
    _parse_skill_file,
    SkillDefinition,
)


class TestResolveSkillPrompt:
    """resolve_skill_prompt() 测试"""

    def test_no_variables(self):
        skill = SkillDefinition(
            name="test", description="", when_to_use=None, allowed_tools=None,
            user_invocable=True, context="inline", prompt_template="plain text",
            source="user", skill_dir="/tmp",
        )
        result = resolve_skill_prompt(skill, "")
        assert result == "plain text"

    def test_args_substitution(self):
        skill = SkillDefinition(
            name="test", description="", when_to_use=None, allowed_tools=None,
            user_invocable=True, context="inline",
            prompt_template="Hello $ARGUMENTS, do your thing.",
            source="user", skill_dir="/tmp",
        )
        result = resolve_skill_prompt(skill, "world")
        assert result == "Hello world, do your thing."

    def test_braced_args(self):
        skill = SkillDefinition(
            name="test", description="", when_to_use=None, allowed_tools=None,
            user_invocable=True, context="inline",
            prompt_template="Hello ${ARGUMENTS}!",
            source="user", skill_dir="/tmp",
        )
        result = resolve_skill_prompt(skill, "bob")
        assert result == "Hello bob!"

    def test_skill_dir_substitution(self):
        skill = SkillDefinition(
            name="test", description="", when_to_use=None, allowed_tools=None,
            user_invocable=True, context="inline",
            prompt_template="Dir: ${CLAUDE_SKILL_DIR}",
            source="user", skill_dir="/my/skills",
        )
        result = resolve_skill_prompt(skill, "")
        assert "/my/skills" in result


class TestBuildSkillDescription:
    """build_skill_description() 测试"""

    def test_empty_skills(self, monkeypatch):
        monkeypatch.setattr("src.prompt.skills.skills._cached_skills", [])
        result = build_skill_description()
        assert result == ""

    def test_with_skills(self, monkeypatch):
        skills = [
            SkillDefinition(
                name="test-skill", description="A test skill",
                when_to_use=None, allowed_tools=None, user_invocable=True,
                context="inline", prompt_template="do test",
                source="user", skill_dir="/tmp",
            ),
        ]
        monkeypatch.setattr("src.prompt.skills.skills._cached_skills", skills)
        result = build_skill_description()
        assert "test-skill" in result
        assert "A test skill" in result


class TestParseSkillFile:
    """_parse_skill_file() 测试"""

    def test_valid_skill(self, tmp_path, monkeypatch):
        skill_file = tmp_path / "test-skill.md"
        skill_file.write_text("""---
name: Test Skill
description: A test skill for unit testing
---

Do the test thing.
""", encoding="utf-8")

        skill_def = _parse_skill_file(skill_file, "user", str(tmp_path))
        assert skill_def is not None
        assert skill_def.name == "Test Skill"
        assert "unit testing" in skill_def.description
        assert "Do the test thing" in skill_def.prompt_template

    def test_no_frontmatter(self, tmp_path):
        skill_file = tmp_path / "no-skill.md"
        skill_file.write_text("just some text, no frontmatter", encoding="utf-8")

        result = _parse_skill_file(skill_file, "user", str(tmp_path))
        # 没有 frontmatter 但有 body，name 使用目录名
        assert result is not None
        # name 回退为目录名
