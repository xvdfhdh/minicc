from __future__ import annotations
from pathlib import Path
from typing import TypedDict
from src.prompt.skills.skills import (
    discover_skills,
    execute_skill,
    get_skill_by_name,
    resolve_skill_prompt,
    SkillDefinition,
    _execute_skill_tool,
)
import re
import subprocess
import asyncio
import requests
import os, json
from typing import Any, Optional
import inspect

from typing import Any, Optional, TypedDict

# 支持并发执行（无需等待结果即可启动下一个）的工具集合
CONCURRENCY_SAFE_TOOLS = {"read_file", "list_files", "grep_search", "web_fetch"}

# 只读工具集合（无需确认）
READ_TOOLS = {"read_file", "list_files", "grep_search", "web_fetch", "tool_search"}

# 编辑工具集合（可能触发确认/拒绝）
EDIT_TOOLS = {"write_file", "edit_file"}

MAX_RESULT_CHARS = 50000

# 危险命令匹配模式（用于触发确认）
DANGEROUS_PATTERNS = [
    re.compile(r"\brm\s"),
    re.compile(r"\bgit\s+(push|reset|clean|checkout\s+\.)"),
    re.compile(r"\bsudo\b"),
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\s"),
    re.compile(r">\s*/dev/"),
    re.compile(r"\bkill\b"),
    re.compile(r"\bpkill\b"),
    re.compile(r"\breboot\b"),
    re.compile(r"\bshutdown\b"),
    re.compile(r"\bdel\s", re.IGNORECASE),
    re.compile(r"\brmdir\s", re.IGNORECASE),
    re.compile(r"\bformat\s", re.IGNORECASE),
    re.compile(r"\btaskkill\s", re.IGNORECASE),
    re.compile(r"\bRemove-Item\s", re.IGNORECASE),
    re.compile(r"\bStop-Process\s", re.IGNORECASE),
]

# 基础工具定义（向模型暴露的工具列表）
tool_definitions: list[ToolDef] = [
    {
        "name": "read_file",
        "description": "Read the content of a file. Returns the file content with line numbers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "The path to the file to read"
                }
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file. Returns the file content with line numbers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "The path to the file to write"
                },
                "content": {
                    "type": "string",
                    "description": "The content to write to the file"
                }
            },
            "required": ["file_path", "content"],
        },
    },
    {
        "name": "save_memory",
        "description": "Save a persistent memory to the memory directory (~/.minicc/memory/). Use this to remember user preferences, project context, feedback, or reference information. Memories are stored as YAML-frontmatter markdown files.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Short descriptive name for the memory (e.g. '用户偏好语言')"
                },
                "type": {
                    "type": "string",
                    "description": "Memory type: 'user' (preferences/knowledge), 'feedback' (corrections), 'project' (goals/decisions), 'reference' (pointers)"
                },
                "description": {
                    "type": "string",
                    "description": "One-line summary of what this memory contains"
                },
                "content": {
                    "type": "string",
                    "description": "The full content of the memory"
                }
            },
            "required": ["name", "type", "description", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Edit a file by replacing a unique occurrence of old_string with new_string. The old_string must match exactly one location in the file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "The path to the file to edit"
                },
                "old_string": {
                    "type": "string",
                    "description": "The exact text to be replaced. Must uniquely identify a single location in the file."
                },
                "new_string": {
                    "type": "string",
                    "description": "The new text to replace old_string with"
                }
            },
            "required": ["file_path", "old_string", "new_string"],
        },
    },
    {
        "name": "list_files",
        "description": "List all files in the current directory. Returns a list of file names.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "The path to the directory to list"
                }
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "grep_search",
        "description": "Search for a pattern in files recursively using grep. Returns matching lines with line numbers (up to 100 matches).",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "The pattern (regular expression) to search for"
                },
                "file_path": {
                    "type": "string",
                    "description": "The file or directory path to search in. Defaults to the current directory."
                },
                "include": {
                    "type": "string",
                    "description": "Glob pattern to filter which files to search, e.g. '*.py' or '*.ts'"
                }
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "run_shell",
        "description": "Execute a shell command and return its stdout. On failure, returns the exit code along with stdout and stderr.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute"
                },
                "timeout": {
                    "type": "number",
                    "description": "Maximum seconds to wait before the command times out. Defaults to 30."
                }
            },
            "required": ["command"],
        },
    },
    {
        "name": "web_fetch",
        "description": "Fetch a URL and return its content as text. For HTML pages, tags are stripped.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to fetch"},
                "max_length": {"type": "number", "description": "Maximum content length (default 50000)"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "skill",
        "description": "Invoke a registered skill by name. Skills are user-defined prompt templates stored in ~/.minicc/skills/",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string", "description": "Name of the skill to invoke"},
                "args": {"type": "string", "description": "Optional arguments to pass to the skill prompt template"},
            },
            "required": ["skill_name"],
        },
    },
    {
        "name": "enter_plan_mode",
        "description": "Enter plan mode to switch to a read-only planning phase. ...",
        "input_schema": {"type": "object", "properties": {}},
        "deferred": True,
    },
    {
        "name": "exit_plan_mode",
        "description": "Exit plan mode after you have finished writing your plan to the plan file. ...",
        "input_schema": {"type": "object", "properties": {}},
        "deferred": True,
    },
    {
        "name": "agent",
        "description": "Launch a sub-agent to handle a task autonomously. Types: 'explore' (read-only), 'plan' (read-only, structured planning), 'general' (full tools).",
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "Short (3-5 word) description of the sub-agent's task"},
                "prompt": {"type": "string", "description": "Detailed task instructions for the sub-agent"},
                "type": {"type": "string", "enum": ["explore", "plan", "general"], "description": "Agent type. Default: general"},
            },
            "required": ["description", "prompt"],
        },
    }
]


# 截断过长结果：保留首尾各一半，中间插入省略标记
def _truncate_result(result: str) -> str:
    if len(result) <= MAX_RESULT_CHARS:
        return result
    keep_each = (MAX_RESULT_CHARS - 60) // 2
    return (
        result[:keep_each]
        + f"\n\n[... truncated {len(result) - keep_each * 2} chars ...]\n\n"
        + result[-keep_each:]
    )


# 检查 shell 命令是否匹配危险模式
def is_dangerous(command: str) -> bool:
    return any(p.search(command) for p in DANGEROUS_PATTERNS)


class ToolDef(TypedDict):
    name: str
    description: str
    input_schema: dict[str, Any]
    deferred: bool  # 可选，标记延迟加载的工具


# 解析权限规则字符串，如 "read_file(*/*.py)" → {"tool": "read_file", "pattern": "*/*.py"}
def _parse_rule(rule: str) -> dict:
    m = re.match(r"^([a-z_]+)\((.+)\)$", rule)
    if m:
        return {"tool": m.group(1), "pattern": m.group(2)}
    return {"tool": rule, "pattern": None}


_cached_rules: dict | None = None


# 从 JSON 文件加载设置，不存在或解析失败返回 None
def _load_settings(path: Path) -> dict | None:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


# 加载权限规则（从 ~/.minicc/settings.json 和 .minicc/settings.json）
def load_permission_rules() -> dict:
    global _cached_rules
    if _cached_rules is not None:
        return _cached_rules

    allow: list[dict] = []
    deny: list[dict] = []

    user_settings = _load_settings(Path.home() / ".minicc" / "settings.json")
    project_settings = _load_settings(Path.cwd() / ".minicc" / "settings.json")

    for settings in [user_settings, project_settings]:
        if not settings or "permissions" not in settings:
            continue
        perms = settings["permissions"]
        for r in perms.get("allow", []):
            allow.append(_parse_rule(r))
        for r in perms.get("deny", []):
            deny.append(_parse_rule(r))
    _cached_rules = {"allow": allow, "deny": deny}
    return _cached_rules


# 检查单条权限规则是否匹配当前工具调用
def _matches_rule(rule: dict, tool_name: str, inp: dict) -> bool:
    if rule["tool"] != tool_name:
        return False
    if rule["pattern"] is None:
        return True

    value = ""
    if tool_name == "run_shell":
        value = inp.get("command", "")
    elif "file_path" in inp:
        value = inp["file_path"]
    else:
        return True

    pattern = rule["pattern"]
    if pattern.endswith("*"):
        return value.endswith(pattern[:-1])
    return value == pattern


# 在权限规则中查找匹配（deny 优先于 allow）
def _check_permission_rules(tool_name: str, inp: dict) -> str | None:
    rules = load_permission_rules()

    for rule in rules["deny"]:
        if _matches_rule(rule, tool_name, inp):
            return "deny"
    for rule in rules["allow"]:
        if _matches_rule(rule, tool_name, inp):
            return "allow"
    return None


# 完整的权限检查：规则 → 模式限制 → 危险检测
def check_permission(
    tool_name: str, inp: dict, mode: str = "default", plan_file_path: str | None = None
) -> dict:
    """Returns {"action": "allow"|"deny"|"confirm", "message": ...}"""
    if mode=="plan":
        if tool_name in EDIT_TOOLS:
            file_path = inp.get("file_path") or inp.get("path")
            if plan_file_path and file_path == plan_file_path:
                return {"action": "allow"}
            return {"action": "deny", "message": f"Blocked in plan mode: {tool_name}"}
        if tool_name == "run_shell":
            return {"action": "deny", "message": "Shell commands blocked in plan mode"}
    if tool_name in ("enter_plan_mode", "exit_plan_mode"):
        return {"action": "allow"}

    if mode == "bypassPermission":
        return {"action": "allow"}

    rule_result = _check_permission_rules(tool_name, inp)
    if rule_result == "deny":
        return {"action": "deny", "message": f"Denied by permission rule for {tool_name}"}

    if rule_result == "allow":
        return {"action": "allow"}

    if tool_name in READ_TOOLS:
        return {"action": "allow"}

    if mode == "acceptEdits" and tool_name in EDIT_TOOLS:
        return {"action": "allow"}

    # Layer 2: 内置危险模式检查
    needs_confirm = False
    confirm_message = ""

    if tool_name == "run_shell" and is_dangerous(inp.get("command", "")):
        needs_confirm = True
        confirm_message = inp.get("command", "")
    elif tool_name == "write_file" and not Path(inp.get("file_path", "")).exists():
        needs_confirm = True
        confirm_message = f"write new file: {inp.get('file_path', '')}"
    elif tool_name == "edit_file" and not Path(inp.get("file_path", "")).exists():
        needs_confirm = True
        confirm_message = f"edit non-existent file: {inp.get('file_path', '')}"

    if needs_confirm:
        if mode == "dontAsk":
            return {"action": "deny", "message": f"Auto-denied (dontAsk mode): {confirm_message}"}
        return {"action": "confirm", "message": confirm_message}

    return {"action": "allow"}


# 分发执行工具调用：按名称路由到对应 handler，包含读前校验和写后登记
async def execute_tool(
    name: str,
    inp: dict,
    read_file_state: Optional[dict[str, float]] = None,
) -> str:
    handlers = {
        "read_file": _read_file,
        "write_file": _write_file,
        "edit_file": _edit_file,
        "list_files": _list_files,
        "grep_search": _grep_search,
        "run_shell": _run_shell,
        "web_fetch": web_fetch,
        "tool_search": tool_search,
        "skill": _execute_skill_tool,
        "save_memory": _save_memory_tool,
    }

    handler = handlers.get(name)
    if handler is None:
        return f"Tool {name} not found."

    # 写/编辑前校验：必须先读过文件且未被外部修改
    if name in ("write_file", "edit_file"):
        abs_path = os.path.abspath(inp["file_path"])
        if read_file_state is not None and os.path.exists(abs_path):
            if abs_path not in read_file_state:
                return "Error: You must read this file before writing. Use read_file first."
            cur = os.stat(abs_path).st_mtime * 1000
            if cur != read_file_state[abs_path]:
                return "Warning: file was modified externally. Please read_file again."

    # 真正执行 handler（兼容同步和异步）
    result = handler(inp)
    if inspect.isawaitable(result):
        result = await result

    # 执行后登记文件 mtime
    if name in ("read_file", "write_file", "edit_file") and read_file_state is not None:
        if isinstance(result, str) and not result.startswith("Error"):
            abs_path = os.path.abspath(inp["file_path"])
            try:
                read_file_state[abs_path] = os.stat(abs_path).st_mtime * 1000
            except OSError:
                pass

    return _truncate_result(result)


# 生成编辑前后的 unified diff 预览
def _generate_diff(old_content: str, old_string: str, new_string: str) -> str:
    import difflib

    old_lines = old_content.splitlines(keepends=True)
    new_lines = old_content.replace(old_string, new_string, 1).splitlines(keepends=True)

    idx = old_content.index(old_string)
    line_no = old_content[:idx].count("\n") + 1

    diff = difflib.unified_diff(
        old_lines, new_lines,
        fromfile="before", tofile="after",
        lineterm="",
    )
    diff_lines = list(diff)
    start = max(0, line_no - 3)
    end = min(len(diff_lines), line_no + 3)
    return "\n".join(diff_lines[start:end])


# 读取文件并返回带行号的文本
def _read_file(inp: dict) -> str:
    try:
        content = Path(inp["file_path"]).read_text(encoding="utf-8")
        lines = content.split("\n")
        numbered = "\n".join([f"{i+1:4d}| {line}" for i, line in enumerate(lines)])
        return numbered
    except Exception as e:
        return f"Error reading file: {str(e)}"


# 编辑文件：在文件中找到唯一匹配的 old_string 替换为 new_string
def _save_memory_tool(inp: dict) -> str:
    """将 save_memory 工具调用桥接到 memory.save_memory()"""
    try:
        from src.memory.memory import save_memory
        filename = save_memory(
            name=inp["name"],
            description=inp["description"],
            type=inp["type"],
            content=inp["content"],
        )
        return f"Memory saved: {filename}"
    except Exception as e:
        return f"Error saving memory: {e}"


def _edit_file(inp: dict) -> str:
    try:
        path = Path(inp["file_path"])
        content = path.read_text(encoding="utf-8")

        actual = _find_actual_string(content, inp["old_string"])
        if not actual:
            return f"Error: Old string not found in {inp['file_path']}"

        count = content.count(actual)
        if count > 1:
            return f"Error: old_string found {count} times in {inp['file_path']}. Must be unique."

        new_content = content.replace(actual, inp["new_string"], 1)
        path.write_text(new_content, encoding="utf-8")

        # 如果编辑的是 memory 目录，自动刷新索引
        try:
            from src.memory.memory import get_memory_dir, _update_memory_index
            if path.parent == get_memory_dir() and path.suffix == ".md" and path.name != "MEMORY.md":
                _update_memory_index()
        except Exception:
            pass

        diff = _generate_diff(content, actual, inp["new_string"])
        quote_note = " (match via quote normalization)" if actual != inp["old_string"] else ""
        return f"Successfully edited {inp['file_path']}{quote_note}\n\n{diff}"
    except Exception as e:
        return f"Error editing file: {e}"


# 列出目录内容，目录名后加 "/"
def _list_files(inp: dict) -> str:
    try:
        base = Path(inp["file_path"])
        if not base.exists():
            return f"Error: Path not found: {inp['file_path']}"
        if not base.is_dir():
            return f"Error: Not a directory: {inp['file_path']}"

        entries = []
        for p in sorted(base.iterdir()):
            entries.append(f"{p.name}/" if p.is_dir() else p.name)

        if not entries:
            return f"(empty directory: {inp['file_path']})"
        return "".join(entries)
    except Exception as e:
        return f"Error listing files: {e}"


# 将智能引号（Unicode）规范化为 ASCII 引号
def _normalize_quotes(s: str) -> str:
    s = re.sub("[\u2018\u2019\u2032]", "'", s)
    s = re.sub('[\u201c\u201d\u2033]', '"', s)
    return s


# 查找字符串：先精确匹配，失败后尝试引号规范化匹配
def _find_actual_string(file_content: str, search_string: str) -> str | None:
    if search_string in file_content:
        return search_string
    norm_search = _normalize_quotes(search_string)
    norm_file = _normalize_quotes(file_content)
    idx = norm_file.find(norm_search)
    if idx != -1:
        return file_content[idx:idx + len(search_string)]
    return None


# 写入文件（自动创建父目录），返回带行号的预览
def _write_file(inp: dict) -> str:
    try:
        path = Path(inp["file_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(inp["content"], encoding="utf-8")

        # 如果写入的是 memory 目录，自动刷新索引
        from src.memory.memory import get_memory_dir, _update_memory_index
        try:
            if path.parent == get_memory_dir() and path.suffix == ".md" and path.name != "MEMORY.md":
                _update_memory_index()
        except Exception:
            pass

        lines = inp["content"].split("\n")
        line_count = len(lines)
        preview = "\n".join(f"{i+1:4d} | {l}" for i, l in enumerate(lines[:30]))
        trunc = f"\n  ... ({line_count} lines total)" if line_count > 30 else ""
        return f"Successfully wrote to {inp['file_path']} ({line_count} lines)\n\n{preview}{trunc}"
    except Exception as e:
        return f"Error writing file: {e}"


# 使用 Python 实现递归文本搜索（替代系统 grep，跨平台兼容）
def _grep_search(inp: dict) -> str:
    pattern = inp["pattern"]
    path = inp.get("file_path") or "."
    include = inp.get("include")

    try:
        regex = re.compile(pattern)
        base = Path(path)

        if not base.exists():
            return f"Error: Path not found: {path}"

        # 收集待搜索的文件
        if base.is_dir():
            if include:
                files = list(base.rglob(include))
            else:
                files = list(base.rglob("*"))
        else:
            files = [base]

        matches: list[str] = []
        MAX_MATCHES = 100
        for f in files:
            if not f.is_file():
                continue
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
                for line_num, line in enumerate(content.splitlines(), 1):
                    if regex.search(line):
                        matches.append(f"{f}:{line_num}:{line}")
                        if len(matches) >= MAX_MATCHES:
                            break
            except Exception:
                continue
            if len(matches) >= MAX_MATCHES:
                break

        if not matches:
            return "No matches found."
        output = "\n".join(matches)
        if len(matches) >= MAX_MATCHES:
            output += f"\n... and more matches (truncated at {MAX_MATCHES})"
        return output
    except re.error as e:
        return f"Error: Invalid regex pattern: {e}"


# 执行 shell 命令（带超时），返回 stdout 或错误信息
def _run_shell(inp: dict) -> str:
    try:
        timeout = inp.get("timeout", 30)
        result = subprocess.run(
            inp["command"], shell=True, capture_output=True, encoding="utf-8", errors="replace", timeout=timeout
        )
        if result.returncode != 0:
            stderr = f"\nStderr: {result.stderr}" if result.stderr else ""
            stdout = f"\nStdout: {result.stdout}" if result.stdout else ""
            return f"Command failed (exit code {result.returncode}){stdout}{stderr}"
        return result.stdout or "no output"
    except subprocess.TimeoutExpired:
        return f"Command timed out after {inp.get('timeout', 30)} seconds."
    except Exception as e:
        return f"Error: {e}"


# HTTP GET 获取 URL 内容，HTML 页面会去标签
def _detect_encoding(content: bytes, content_type: str) -> str:
    """从 Content-Type 和 HTML meta 标签中检测编码，回退到 UTF-8"""
    # 1. 从 Content-Type header 中提取 charset
    match = re.search(rb'charset\s*=\s*([^\s;]+)', content_type.encode("ascii", errors="ignore"))
    if match:
        return match.group(1).decode("ascii").strip('"\' ')

    # 2. 从 HTML <meta charset> 中提取（前 2048 字节足够）
    head = content[:2048]
    # <meta charset="utf-8"> 或 <meta ... ;charset=utf-8>
    match = re.search(rb'<meta[^>]*charset\s*=\s*["\']?([^"\';>\s]+)', head, re.IGNORECASE)
    if match:
        return match.group(1).decode("ascii").strip('"\' ')

    # 3. 回退到 UTF-8
    return "utf-8"


def web_fetch(input: dict) -> str:
    url = input["url"]
    max_length = input.get("max_length") or 50000
    try:
        res = requests.get(
            url,
            timeout=30,
            headers={"User-Agent": "mini-claude/1.0"},
        )
        if not res.ok:
            result = f"HTTP error: {res.status_code} {res.reason}"
            return result

        # 自动检测编码，避免中文网页乱码
        content_type = res.headers.get("Content-Type", "")
        encoding = _detect_encoding(res.content, content_type)
        try:
            text = res.content.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            text = res.content.decode("utf-8", errors="replace")

        if "html" in content_type or b"<html" in res.content[:512].lower() or b"<!doctype" in res.content[:512].lower():
            text = re.sub(r"<script[\s\S]*?</script>", "", text, flags=re.IGNORECASE)
            text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.IGNORECASE)
            text = re.sub(r"<[^>]*>", " ", text)
            text = text.replace("&nbsp;", " ").replace("&amp;", "&")
            text = re.sub(r"\s{2,}", " ", text)
            text = re.sub(r"\n{3,}", "\n\n", text).strip()

        if len(text) > max_length:
            text = text[:max_length] + f"\n\n[... truncated at {max_length} characters]"

        result = text or "(empty response)"
        return result

    except requests.exceptions.Timeout:
        result = "Error: Request timed out (30s)"
        return result
    except requests.exceptions.RequestException as err:
        result = f"Error fetching {url}: {err}"
        return result


# plan 模式工具定义（延迟加载，需工具搜索激活）
ENTER_PLAN_MODE_DEF = {
    "name": "enter_plan_mode",
    "description": "Enter plan mode to switch to a read-only planning phase...",
    "input_schema": {"type": "object", "properties": {}},
    "deferred": True,
}

# 工具搜索定义（用于发现和激活延迟加载的工具）
TOOL_SEARCH_DEF = {
    "name": "tool_search",
    "description": "Search for available tools by name or keyword. Returns full schemas for matching deferred tools.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Tool name or search keywords"},
        },
        "required": ["query"],
    },
}

activated_tools: set[str] = set()


# 获取当前应暴露给模型的工具列表（过滤掉未激活的延迟工具）
def get_active_tool_definitions(all_tools: Optional[list[dict]] = None) -> list[dict]:
    tools = all_tools if all_tools is not None else tool_definitions
    return [
        {k: v for k, v in t.items() if k not in ["deferred"]}
        for t in tools
        if not t.get("deferred") or t["name"] in activated_tools
    ]


# 搜索延迟工具并按关键词激活
def tool_search(input: dict[str, Any]) -> str:
    query = (input.get("query") or "").lower()
    deferred = [t for t in tool_definitions if t.get("deferred")]
    matches = [
        t for t in deferred
        if query in t["name"].lower()
        or query in (t.get("description") or "").lower()
    ]
    for m in matches:
        activated_tools.add(m["name"])
    return json.dumps(
        [
            {
                "name": t["name"],
                "description": t["description"],
                "input_schema": t["input_schema"],
            }
            for t in matches
        ],
        indent=2,
    )


