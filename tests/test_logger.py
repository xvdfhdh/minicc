"""测试日志系统。"""
from __future__ import annotations
from pathlib import Path
from src.main.logger import get_log_file_path, get_recent_logs


class TestLogger:
    """日志系统测试"""

    def test_get_log_file_path(self):
        path = get_log_file_path()
        assert path.endswith("minicc.log")
        assert ".minicc" in path

    def test_get_recent_logs_no_file(self, tmp_path, monkeypatch):
        non_existent = tmp_path / "nonexistent" / "minicc.log"
        monkeypatch.setattr("src.main.logger.LOG_FILE", non_existent)
        result = get_recent_logs(50)
        assert result == "(no logs yet)"

    def test_get_recent_logs_with_content(self, tmp_path, monkeypatch):
        log_file = tmp_path / "minicc.log"
        monkeypatch.setattr("src.main.logger.LOG_FILE", log_file)
        log_file.parent.mkdir(parents=True, exist_ok=True)

        lines = [f"line {i}" for i in range(100)]
        log_file.write_text("\n".join(lines), encoding="utf-8")

        result = get_recent_logs(10)
        result_lines = result.strip().split("\n")
        assert len(result_lines) <= 10
        assert "line 99" in result

    def test_get_recent_logs_more_than_total(self, tmp_path, monkeypatch):
        log_file = tmp_path / "minicc.log"
        monkeypatch.setattr("src.main.logger.LOG_FILE", log_file)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text("a\nb\nc", encoding="utf-8")

        result = get_recent_logs(50)
        assert len(result.strip().split("\n")) == 3
