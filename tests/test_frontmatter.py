"""测试 frontmatter 解析器。"""
from __future__ import annotations
from src.memory.frontmatter import parse_frontmatter


class TestParseFrontmatter:
    """parse_frontmatter() 单元测试"""

    def test_valid_yaml_frontmatter(self):
        result = parse_frontmatter("---\nname: test\ntype: user\n---\n\nbody text")
        assert result.meta == {"name": "test", "type": "user"}
        assert result.body == "body text"

    def test_empty_content(self):
        result = parse_frontmatter("")
        assert result.meta == {}
        assert result.body == ""

    def test_no_frontmatter_plain_text(self):
        result = parse_frontmatter("just plain text")
        assert result.meta == {}
        assert result.body == "just plain text"

    def test_no_frontmatter_dashes_but_no_opening(self):
        result = parse_frontmatter("hello\n---\nworld")
        assert result.meta == {}
        assert result.body == "hello\n---\nworld"

    def test_unclosed_frontmatter(self):
        result = parse_frontmatter("---\nname: test\n")
        assert result.meta == {}
        assert result.body == "---\nname: test\n"

    def test_value_with_colon(self):
        result = parse_frontmatter("---\ndesc: a: b\n---\n\nok")
        assert result.meta == {"desc": "a: b"}
        assert result.body == "ok"

    def test_multiline_body(self):
        result = parse_frontmatter("---\nname: x\n---\n\nline1\nline2")
        assert result.body == "line1\nline2"

    def test_dashes_in_body(self):
        result = parse_frontmatter("---\nname: x\n---\n\ntext --- more")
        assert result.body == "text --- more"

    def test_multiple_keys(self):
        content = "---\nname: foo\ntype: bar\ndescription: baz desc\n---\n\nhello world"
        result = parse_frontmatter(content)
        assert result.meta == {"name": "foo", "type": "bar", "description": "baz desc"}
        assert result.body == "hello world"

    def test_empty_value(self):
        result = parse_frontmatter("---\nname: \ntype: user\n---\n\nbody")
        assert result.meta == {"name": "", "type": "user"}

    def test_extra_whitespace_around_separators(self):
        result = parse_frontmatter("---  \nname: test\n  ---  \n\nbody")
        assert result.meta == {"name": "test"}
        assert result.body == "body"
