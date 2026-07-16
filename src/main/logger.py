"""日志系统初始化，基于 loguru。"""
from __future__ import annotations
import sys
from pathlib import Path
from loguru import logger

LOG_DIR = Path.home() / ".minicc" / "logs"
LOG_FILE = LOG_DIR / "minicc.log"

# 移除默认 handler
logger.remove()

# 终端输出：仅 WARNING 以上，保留 Rich 作为主 UI
logger.add(
    sys.stderr,
    level="WARNING",
    format="<red>{level: <8}</red> | <level>{message}</level>",
    colorize=True,
)

# 文件输出：所有级别（DEBUG 起），按日轮转，保留 7 天
LOG_DIR.mkdir(parents=True, exist_ok=True)
logger.add(
    LOG_FILE,
    level="DEBUG",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
    rotation="00:00",
    retention="7 days",
    encoding="utf-8",
    enqueue=True,
    backtrace=True,
    diagnose=True,
)


def get_log_file_path() -> str:
    """返回当前日志文件路径。"""
    return str(LOG_FILE)


def get_recent_logs(lines: int = 50) -> str:
    """读取最近 N 行日志。"""
    if not LOG_FILE.exists():
        return "(no logs yet)"
    try:
        content = LOG_FILE.read_text(encoding="utf-8")
        all_lines = content.strip().split("\n")
        return "\n".join(all_lines[-lines:])
    except Exception:
        return "(unable to read logs)"
