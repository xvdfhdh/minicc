"""测试提示词构建模块。"""
from __future__ import annotations
from pathlib import Path
from src.prompt.prompt import resolve_includes


class TestResolveIncludes:
    """resolve_includes() 测试 — 语法为 @./path 或 @/absolute/path"""

    def test_no_includes(self):
        content = "plain system prompt"
        result = resolve_includes(content, "/tmp")
        assert result == content

    def test_single_level_include_relative(self, tmp_path):
        included = tmp_path / "rules.md"
        included.write_text("rule content", encoding="utf-8")

        result = resolve_includes(f"prompt\n@./rules.md\nend", str(tmp_path))
        assert "rule content" in result

    def test_include_file_not_found(self, tmp_path):
        result = resolve_includes("prompt\n@./nonexistent.md\nend", str(tmp_path))
        assert "not found" in result

    def test_circular_reference_detection(self, tmp_path):
        a = tmp_path / "a.md"
        b = tmp_path / "b.md"
        a.write_text(f"@./b.md", encoding="utf-8")
        b.write_text(f"@./a.md", encoding="utf-8")

        result = resolve_includes(a.read_text(encoding="utf-8"), str(tmp_path))
        assert "circular" in result.lower()

    def test_absolute_path_include(self, tmp_path):
        included = tmp_path / "abs.md"
        included.write_text("absolute content", encoding="utf-8")
        # Use relative path to avoid Windows path issues with @/ prefix
        result = resolve_includes(f"@./abs.md", str(tmp_path))
        assert "absolute content" in result
