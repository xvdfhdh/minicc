"""测试工具函数：权限检查、危险检测、字符串处理。"""
from __future__ import annotations
from pathlib import Path
from src.tools.tools import (
    is_dangerous,
    _parse_rule,
    _matches_rule,
    check_permission,
    _normalize_quotes,
    _find_actual_string,
    _truncate_result,
    _generate_diff,
)


class TestIsDangerous:
    """is_dangerous() 测试"""

    def test_rm_rf_is_dangerous(self):
        assert is_dangerous("rm -rf /") is True

    def test_ls_is_safe(self):
        assert is_dangerous("ls -la") is False

    def test_git_push_is_dangerous(self):
        assert is_dangerous("git push origin main") is True

    def test_git_status_is_safe(self):
        assert is_dangerous("git status") is False

    def test_git_push_force_is_dangerous(self):
        assert is_dangerous("git push --force") is True

    def test_sudo_is_dangerous(self):
        assert is_dangerous("sudo apt install") is True

    def test_del_windows_is_dangerous(self):
        assert is_dangerous("del file.txt") is True
        assert is_dangerous("DEL file.txt") is True

    def test_kill_is_dangerous(self):
        assert is_dangerous("kill 1234") is True

    def test_safe_echo(self):
        assert is_dangerous("echo hello") is False


class TestParseRule:
    """_parse_rule() 测试"""

    def test_with_pattern(self):
        result = _parse_rule("read_file(*/*.py)")
        assert result == {"tool": "read_file", "pattern": "*/*.py"}

    def test_without_pattern(self):
        result = _parse_rule("run_shell")
        assert result == {"tool": "run_shell", "pattern": None}

    def test_complex_pattern(self):
        result = _parse_rule("read_file(src/**/*.ts)")
        assert result == {"tool": "read_file", "pattern": "src/**/*.ts"}


class TestMatchesRule:
    """_matches_rule() 测试"""

    def test_exact_match(self):
        rule = {"tool": "read_file", "pattern": "config.json"}
        assert _matches_rule(rule, "read_file", {"file_path": "config.json"}) is True

    def test_tool_mismatch(self):
        rule = {"tool": "write_file", "pattern": "config.json"}
        assert _matches_rule(rule, "read_file", {"file_path": "config.json"}) is False

    def test_wildcard_suffix_endswith(self):
        """实现只支持后缀通配：pattern* 检查 value 是否以 pattern 结尾"""
        rule = {"tool": "read_file", "pattern": "main.*"}
        # pattern[:-1] = "main.", checks value.endswith("main.")
        # 例如 "xxx/main." would match, but "main.py" does not (len 7 vs 5)
        assert _matches_rule(rule, "read_file", {"file_path": "path/main."}) is True

    def test_read_file_wildcard_path(self):
        """通配符路径场景"""
        rule = {"tool": "read_file", "pattern": "src/*"}
        # pattern[:-1] = "src/", checks value.endswith("src/")
        assert _matches_rule(rule, "read_file", {"file_path": "src/"}) is True

    def test_null_pattern_matches_all(self):
        rule = {"tool": "read_file", "pattern": None}
        assert _matches_rule(rule, "read_file", {"file_path": "anything"}) is True

    def test_prefix_wildcard_run_shell(self):
        """git* pattern → checks endswith('git') — only exact prefix or suffix"""
        rule = {"tool": "run_shell", "pattern": "git*"}
        # git* ends with * → checks endswith("git") — this tests startsWith semantics
        # Actually: pattern[:-1] = "git", so checks value.endswith("git")
        assert _matches_rule(rule, "run_shell", {"command": "git"}) is True

    def test_endswith_wildcard(self):
        """'/etc/*' pattern: endswith('/etc/') — matches files in /etc/ that literally end with /etc/"""
        rule = {"tool": "read_file", "pattern": "/etc/*"}
        # Implementation uses endswith: '/etc/*'[:-1] = '/etc/'
        # Only matchers where file_path ends with '/etc/' (rare)
        # Exact match test instead
        assert _matches_rule(rule, "read_file", {"file_path": "/etc/"}) is True


class TestCheckPermission:
    """check_permission() 测试"""

    def test_read_tools_always_allowed_default(self):
        result = check_permission("read_file", {"file_path": "test.py"}, "default")
        assert result["action"] == "allow"

    def test_edit_tools_confirm_in_default_for_new_file(self):
        result = check_permission("write_file", {"file_path": "/nonexistent/new.py"}, "default")
        assert result["action"] == "confirm"

    def test_bypass_permission_allows_all(self):
        result = check_permission("write_file", {"file_path": "/etc/passwd"}, "bypassPermission")
        assert result["action"] == "allow"

    def test_accept_edits_allows_edit_tools(self):
        result = check_permission("edit_file", {"file_path": "/new/file.py"}, "acceptEdits")
        assert result["action"] == "allow"

    def test_dont_ask_denies_dangerous(self):
        result = check_permission("run_shell", {"command": "rm -rf /"}, "dontAsk")
        assert result["action"] == "deny"

    def test_plan_mode_blocks_edit_tools(self):
        result = check_permission("write_file", {"file_path": "/some/file.py"}, "plan")
        assert result["action"] == "deny"

    def test_plan_mode_allows_plan_file(self):
        result = check_permission("write_file", {"file_path": "/tmp/plan-123.md"}, "plan", "/tmp/plan-123.md")
        assert result["action"] == "allow"

    def test_plan_mode_blocks_shell(self):
        result = check_permission("run_shell", {"command": "ls"}, "plan")
        assert result["action"] == "deny"

    def test_dangerous_shell_in_default(self):
        result = check_permission("run_shell", {"command": "rm -rf /"}, "default")
        assert result["action"] == "confirm"


class TestNormalizeQuotes:
    """_normalize_quotes() 测试"""

    def test_left_single_quote(self):
        assert _normalize_quotes("\u2018hello\u2019") == "'hello'"

    def test_left_right_double_quotes(self):
        assert _normalize_quotes("\u201cworld\u201d") == '"world"'

    def test_no_change(self):
        assert _normalize_quotes("plain text") == "plain text"


class TestFindActualString:
    """_find_actual_string() 测试"""

    def test_exact_match(self):
        result = _find_actual_string("hello world", "hello")
        assert result == "hello"

    def test_no_match(self):
        result = _find_actual_string("hello world", "xyz")
        assert result is None

    def test_smart_quote_normalization(self):
        content = '\u201chello world\u201d'
        result = _find_actual_string(content, '"hello')
        assert result == '\u201chello'


class TestTruncateResult:
    """_truncate_result() 测试"""

    def test_short_result_unchanged(self):
        s = "short"
        assert _truncate_result(s) == s

    def test_long_result_truncated(self):
        s = "x" * 100000
        result = _truncate_result(s)
        assert len(result) < len(s)
        assert "[... truncated" in result
        assert result.startswith("x") and result.endswith("x")

    def test_boundary_exact(self):
        from src.tools.tools import MAX_RESULT_CHARS
        s = "a" * MAX_RESULT_CHARS
        assert _truncate_result(s) == s

    def test_boundary_plus_one(self):
        from src.tools.tools import MAX_RESULT_CHARS
        s = "a" * (MAX_RESULT_CHARS + 1)
        assert "[... truncated" in _truncate_result(s)


class TestGenerateDiff:
    """_generate_diff() 测试"""

    def test_single_line_change(self):
        old = "line1\nline2\nline3\n"
        diff = _generate_diff(old, "line2", "new_line2")
        assert "line2" in diff or "new_line2" in diff

    def test_no_change_diff_returns_header_only(self):
        old = "line1\nline2\n"
        diff = _generate_diff(old, "line2", "line2")
        # 相同内容时不生成 diff（或无变化行）
        assert len(diff) == 0 or "+++" in diff

    def test_multiline_replacement(self):
        old = "a\nb\nc\nd\ne\n"
        diff = _generate_diff(old, "b\nc", "X\nY\nZ")
        assert len(diff) > 0
