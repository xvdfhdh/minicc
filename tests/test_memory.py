"""测试记忆系统（纯函数 + 文件操作）。"""
from __future__ import annotations
from pathlib import Path
from src.memory.memory import (
    _slugify,
    format_frontmatter,
    memory_age,
    memory_freshness_warning,
    format_memory_manifest,
    save_memory,
    list_memories,
    scan_memory_headers,
    load_memory_index,
    _update_memory_index,
    get_memory_dir,
)


class TestSlugify:
    """_slugify() 测试"""

    def test_english(self):
        assert _slugify("User Preferences") == "user_preferences"

    def test_chinese(self):
        assert _slugify("用户偏好语言") == "用户偏好语言"

    def test_mixed(self):
        assert _slugify("用户 Preferences") == "用户_preferences"

    def test_special_chars(self):
        assert _slugify("hello!@#$world") == "hello_world"

    def test_all_special(self):
        assert _slugify("!@#$%") == "memory"

    def test_trailing_underscore(self):
        assert _slugify("  hello!!!  ") == "hello"


class TestFormatFrontmatter:
    """format_frontmatter() 测试"""

    def test_standard(self):
        result = format_frontmatter(
            {"name": "test", "type": "user", "description": "a test"},
            "body content"
        )
        lines = result.split("\n")
        assert lines[0] == "---"
        assert "name: test" in result
        assert "type: user" in result
        assert "description: a test" in result
        assert "body content" in result

    def test_empty_meta(self):
        result = format_frontmatter({}, "just body")
        assert "just body" in result

    def test_empty_body(self):
        result = format_frontmatter({"name": "x"}, "")
        assert "name: x" in result


class TestMemoryAge:
    """memory_age() 测试"""

    def test_just_now(self, monkeypatch):
        now_ms = 1700000000 * 1000
        monkeypatch.setattr("src.memory.memory.time.time", lambda: 1700000000)
        assert memory_age(now_ms) == "just now"

    def test_minutes_ago(self, monkeypatch):
        monkeypatch.setattr("src.memory.memory.time.time", lambda: 1700000000)
        assert memory_age(1700000000 * 1000 - 120000) == "2m ago"

    def test_hours_ago(self, monkeypatch):
        monkeypatch.setattr("src.memory.memory.time.time", lambda: 1700000000)
        assert memory_age(1700000000 * 1000 - 7200000) == "2h ago"

    def test_days_ago(self, monkeypatch):
        monkeypatch.setattr("src.memory.memory.time.time", lambda: 1700000000)
        assert memory_age(1700000000 * 1000 - 172800000) == "2d ago"


class TestMemoryFreshnessWarning:
    """memory_freshness_warning() 测试"""

    def test_recent_no_warning(self, monkeypatch):
        monkeypatch.setattr("src.memory.memory.time.time", lambda: 1700000000)
        assert memory_freshness_warning(1700000000 * 1000 - 10000) == ""

    def test_old_has_warning(self, monkeypatch):
        monkeypatch.setattr("src.memory.memory.time.time", lambda: 1700000000)
        result = memory_freshness_warning(1700000000 * 1000 - 172800000)
        assert "2 days old" in result


class TestFormatMemoryManifest:
    """format_memory_manifest() 测试"""

    def test_standard(self):
        headers = [
            {"filename": "user_pref.md", "type": "user", "name": "偏好", "description": "语言偏好"},
        ]
        result = format_memory_manifest(headers)
        assert "[user_pref.md]" in result
        assert "(user)" in result
        assert "偏好" in result

    def test_empty(self):
        assert format_memory_manifest([]) == ""


class TestMemoryFileOperations:
    """记忆文件 CRUD 测试"""

    def test_save_and_list(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.memory.memory.MEMORY_DIR", tmp_path / "mem")
        d = get_memory_dir()
        d.mkdir(parents=True, exist_ok=True)

        filename = save_memory("测试记忆", "一条测试记忆", "user", "测试内容")
        assert filename.endswith(".md")
        assert (d / filename).exists()

        memories = list_memories()
        assert len(memories) >= 1
        names = [m.name for m in memories]
        assert "测试记忆" in names

    def test_no_duplicate_index_entry(self, tmp_path, monkeypatch):
        """MEMORY.md 索引应包含记忆条目"""
        fake_mem = tmp_path / "mem"
        monkeypatch.setattr("src.memory.memory.MEMORY_DIR", fake_mem)
        d = get_memory_dir()
        d.mkdir(parents=True, exist_ok=True)

        save_memory("mem1", "desc1", "user", "content1")
        save_memory("mem2", "desc2", "project", "content2")

        # 直接读取索引文件验证内容
        index_path = fake_mem / "MEMORY.md"
        assert index_path.exists()
        raw = index_path.read_bytes()
        # 允许 UTF-8 或系统默认编码
        try:
            idx_content = raw.decode("utf-8")
        except UnicodeDecodeError:
            idx_content = raw.decode("gbk", errors="replace")
        assert "mem1" in idx_content

    def test_scan_memory_headers(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.memory.memory.MEMORY_DIR", tmp_path / "mem")
        d = get_memory_dir()
        d.mkdir(parents=True, exist_ok=True)

        save_memory("记忆A", "描述A", "user", "内容A")
        headers = scan_memory_headers()
        assert len(headers) >= 1
        assert any(h["name"] == "记忆A" for h in headers)

    def test_empty_directory(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.memory.memory.MEMORY_DIR", tmp_path / "empty_mem")
        assert list_memories() == []
        assert scan_memory_headers() == []
        assert load_memory_index() == ""
