from __future__ import annotations
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Awaitable

MAX_MEMORY_BYTES_PER_FILE = 4096
MAX_SESSION_MEMORY_BYTES = 20000
SELECT_MEMORIES_PROMPT = """You are selecting memories that will be useful to an AI coding assistant as it processes a user's query. You will be given the user's query and a list of available memory files with their filenames and descriptions.

Return a JSON object with a "selected_memories" array of filenames for the memories that will clearly be useful (up to 5). Only include memories that you are certain will be helpful based on their name and description.
- If you are unsure if a memory will be useful, do not include it.
- If no memories would clearly be useful, return an empty array."""


async def select_relevant_memories(
    query: str,
    side_query: Callable[[str, str], Awaitable[str]],
    already_surfaced: set[str],
    signal: Any | None = None,
) -> list[dict]:
    headers = scan_memory_headers()
    if len(headers) == 0:
        return []

    # 过滤已经在本会话中展示过的记忆
    candidates = [h for h in headers if h["filePath"] not in already_surfaced]
    if len(candidates) == 0:
        return []

    manifest = format_memory_manifest(candidates)

    try:
        text = await side_query(
            SELECT_MEMORIES_PROMPT,
            f"Query: {query}\n\nAvailable memories:\n{manifest}",
            signal,
        )

        # 从响应中提取 JSON（模型可能用 markdown 代码块包裹）
        json_match = re.search(r"\{[\s\S]*\}", text)
        if not json_match:
            return []

        parsed = json.loads(json_match.group(0))
        selected_filenames: list[str] = parsed.get("selected_memories") or []

        # 文件名映射回 header，读取完整内容
        filename_set = set(selected_filenames)
        selected = [h for h in candidates if h["filename"] in filename_set]

        results = []
        for h in selected[:5]:
            content = Path(h["filePath"]).read_text(encoding="utf-8")
            # 单文件截断（4KB）
            if len(content.encode("utf-8")) > MAX_MEMORY_BYTES_PER_FILE:
                content = content[:MAX_MEMORY_BYTES_PER_FILE] + "\n\n[... truncated, memory file too large ...]"
            freshness = memory_freshness_warning(h["mtimeMs"])
            if freshness:
                header_text = f"{freshness}\n\nMemory: {h['filePath']}:"
            else:
                header_text = f"Memory (saved {memory_age(h['mtimeMs'])}): {h['filePath']}:"

            results.append({
                "path": h["filePath"],
                "content": content,
                "mtimeMs": h["mtimeMs"],
                "header": header_text,
            })

        return results

    except Exception as err:
        # 静默失败——记忆召回永远不应阻塞主循环
        if signal and hasattr(signal, 'aborted') and signal.aborted:
            return []
        print(f"[memory] semantic recall failed: {err}")
        return []



MAX_INDEX_LINES = 200
MAX_INDEX_BYTES = 25000

def save_memory(name:str,description:str,type:str,content:str)->str:
    d=get_memory_dir()
    filename=f"{type}_{_slugify(name)}.md"
    text=format_frontmatter(
        {"name":name,"description":description,"type":type},
        content
    )
    (d/filename).write_text(text)
    _update_memory_index()
    return filename

def _update_memory_index()->None:
    memories=list_memories()
    lines = ["# Memory Index", ""]
    for m in memories:
        lines.append(f"- **[{m.name}]({m.filename})** ({m.type}) — {m.description}")
    _get_index_path().write_text("\n".join(lines))


def load_memory_index()->str:
    index_path=_get_index_path()
    if not index_path.exists():
        return ""
    content=index_path.read_text()
    lines=content.split("\n")
    if len(lines) > MAX_INDEX_LINES:
        content = "\n".join(lines[:MAX_INDEX_LINES]) + "\n\n[... truncated, too many memory entries ...]"
    if len(content.encode()) > MAX_INDEX_BYTES:
        content = content[:MAX_INDEX_BYTES] + "\n\n[... truncated, index too large ...]"
    return content

def build_memory_prompt_section()->str:
    index=load_memory_index()
    memory_dir=str(get_memory_dir())

    return f"""# Memory System

You have a persistent, file-based memory system at `{memory_dir}`.

## Memory Types
- **user**: User's role, preferences, knowledge level
- **feedback**: Corrections and guidance from the user
- **project**: Ongoing work, goals, deadlines, decisions
- **reference**: Pointers to external resources

## How to Save Memories
Use the write_file tool to create a memory file with YAML frontmatter:
...
Save to: `{memory_dir}/`
Filename format: `{{type}}_{{slugified_name}}.md`

## What NOT to Save
- Code patterns or architecture (read the code instead)
- Git history (use git log)
- Anything already in CLAUDE.md
- Ephemeral task details

{"## Current Memory Index" + chr(10) + index if index else "(No memories saved yet.)"}"""


class MemoryPrefetch:
    """记忆预取句柄——承载异步筛选任务及其状态标记。"""
    def __init__(self, promise):
        self.promise = promise        # asyncio.Task 或协程对象
        self.settled = False          # 任务是否已完成（成功或失败）
        self.consumed = False         # 结果是否已被主循环取走


async def _mark_settled(handle: MemoryPrefetch) -> None:
    """等待任务完成后标记 settled=True，无论成功或失败。"""
    try:
        await handle.promise
    except Exception:
        pass
    handle.settled = True


def start_memory_prefetch(
    query: str,
    side_query: Callable[[str, str], Awaitable[str]],
    already_surfaced: set[str],
    session_memory_bytes: int,
    signal: Any | None = None,
) -> MemoryPrefetch | None:
    # 门控 1: 单词查询跳过（太短，无法语义匹配）
    if not re.search(r"\s", query.strip()):
        return None

    # 门控 2: 会话预算已满
    if session_memory_bytes >= MAX_SESSION_MEMORY_BYTES:
        return None

    # 门控 3: 没有记忆文件
    dir = get_memory_dir()
    has_memories = any(
        f.name.endswith(".md") and f.name != "MEMORY.md"
        for f in dir.iterdir()
        if f.is_file()
    )
    if not has_memories:
        return None

    handle = MemoryPrefetch(
        promise=select_relevant_memories(query, side_query, already_surfaced, signal),
    )

    # 创建后台 Task：完成后标记 settled=True
    asyncio.create_task(_mark_settled(handle))

    return handle


def memory_freshness_warning(mtime_ms: float) -> str:
    days = max(0, int((time.time() * 1000 - mtime_ms) / 86_400_000))
    if days <= 1:
        return ""
    return (
        f"This memory is {days} days old. Memories are point-in-time observations, "
        f"not live state — claims about code behavior may be outdated. "
        f"Verify against current code before asserting as fact."
    )