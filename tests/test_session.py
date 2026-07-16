"""测试会话持久化模块。"""
from __future__ import annotations
import json
from pathlib import Path
from src.main.session import save_session, load_session, list_sessions, get_latest_session_id


class TestSession:
    """会话 CRUD 测试"""

    def test_save_and_load_roundtrip(self, tmp_path, monkeypatch):
        session_dir = tmp_path / "sessions"
        monkeypatch.setattr("src.main.session.SESSION_DIR", session_dir)

        data = {"metadata": {"id": "abc-123"}, "messages": [{"role": "user", "content": "hi"}]}
        save_session("abc-123", data)

        loaded = load_session("abc-123")
        assert loaded is not None
        assert loaded["metadata"]["id"] == "abc-123"
        assert loaded["messages"][0]["content"] == "hi"

    def test_load_nonexistent_session(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.main.session.SESSION_DIR", tmp_path / "sessions")
        result = load_session("nonexistent")
        assert result is None

    def test_load_corrupted_json(self, tmp_path, monkeypatch):
        session_dir = tmp_path / "sessions"
        monkeypatch.setattr("src.main.session.SESSION_DIR", session_dir)
        session_dir.mkdir(parents=True)
        (session_dir / "bad.json").write_text("not valid json{{{")

        result = load_session("bad")
        assert result is None

    def test_list_sessions(self, tmp_path, monkeypatch):
        session_dir = tmp_path / "sessions"
        monkeypatch.setattr("src.main.session.SESSION_DIR", session_dir)
        session_dir.mkdir(parents=True)

        save_session("s1", {"metadata": {"id": "s1", "startTime": "2025-01-01"}})
        save_session("s2", {"metadata": {"id": "s2", "startTime": "2025-06-01"}})

        sessions = list_sessions()
        assert len(sessions) == 2
        ids = {s["metadata"]["id"] for s in sessions}
        assert "s1" in ids
        assert "s2" in ids

    def test_get_latest_session(self, tmp_path, monkeypatch):
        session_dir = tmp_path / "sessions"
        monkeypatch.setattr("src.main.session.SESSION_DIR", session_dir)
        session_dir.mkdir(parents=True)

        save_session("older", {"startTime": "2025-01-01", "id": "older"})
        save_session("newer", {"startTime": "2025-06-01", "id": "newer"})

        latest = get_latest_session_id()
        assert latest == "newer"

    def test_empty_sessions_list(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.main.session.SESSION_DIR", tmp_path / "empty_sessions")
        assert list_sessions() == []
        assert get_latest_session_id() is None
