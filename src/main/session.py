from __future__ import annotations
from pathlib import Path
import json
from typing import Any

SESSION_DIR = Path.home() / ".minicc" / "sessions"


# 将会话数据序列化为 JSON 保存到 ~/.minicc/sessions/
def save_session(session_id: str, data: dict[str, Any]) -> None:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    (SESSION_DIR / f"{session_id}.json").write_text(json.dumps(data, indent=2, default=str))


# 获取最近一次会话的 ID（按 startTime 降序）
def get_latest_session_id() -> str | None:
    sessions = list_sessions()
    if not sessions:
        return None
    sessions.sort(key=lambda s: s.get("startTime", ""), reverse=True)
    return sessions[0].get("id")


# 列出所有已保存的会话
def list_sessions() -> list[dict]:
    if not SESSION_DIR.exists():
        return []
    sessions = []
    for f in SESSION_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            sessions.append(data)
        except Exception:
            pass  # 跳过损坏的文件
    return sessions


# 加载指定 ID 的会话数据
def load_session(session_id: str) -> dict | None:
    path = SESSION_DIR / f"{session_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None
