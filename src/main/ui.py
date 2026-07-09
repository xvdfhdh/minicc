from __future__ import annotations
from main.session import *
import asyncio
import os
import sys
import json
from pathlib import Path
from rich.console import Console
console = Console()


# ── 通用输出函数 ──

def print_error(msg: str) -> None:
    console.print(f"\n[red]Error:[/red] {msg}")

def print_info(msg: str) -> None:
    console.print(f"[cyan]  ● {msg}[/cyan]")

def print_welcome() -> None:
    console.print("\n[bold cyan]Mini Claude Code[/bold cyan] — type [dim]/help[/dim] for commands\n")

def print_user_prompt() -> None:
    console.print("[bold green]>[/bold green] ", end="")

def print_assistant_text(text: str) -> None:
    console.print(text, end="")

def print_cost(input_tokens: int, output_tokens: int) -> None:
    console.print(f"[dim]  (in: {input_tokens}, out: {output_tokens})[/dim]")

def print_divider() -> None:
    console.print("[dim]" + "─" * 50 + "[/dim]")

def print_confirmation(command: str) -> None:
    console.print(f"\n  [yellow]⚠ Danger:[/yellow] [white]{command[:80]}[/white]")

def print_retry(attempt: int, max_retries: int, reason: str) -> None:
    console.print(f"  [yellow]Retry {attempt}/{max_retries}:[/yellow] {reason}")

def stop_spinner() -> None:
    pass  # 终端模式无 spinner


# ── 工具图标映射 ──

_TOOL_ICONS: dict[str, str] = {
    "read_file": "📖",
    "write_file": "📝",
    "edit_file": "✏️",
    "list_files": "📂",
    "grep_search": "🔍",
    "run_shell": "⚡",
    "web_fetch": "🌐",
    "tool_search": "🔎",
    "enter_plan_mode": "📋",
}


# 根据工具名称返回对应 emoji 图标，未注册的返回 🔧
def _get_tool_icon(name: str) -> str:
    return _TOOL_ICONS.get(name, "🔧")


# ── 工具摘要生成 ──


# 将工具调用参数格式化为一行摘要文本
def _get_tool_summary(name: str, inp: dict) -> str:
    if name == "read_file":
        return inp.get("file_path", "")

    if name == "write_file":
        path = inp.get("file_path", "")
        content = inp.get("content", "")
        line_count = len(content.split("\n"))
        return f"{path} ({line_count} lines)"

    if name == "edit_file":
        path = inp.get("file_path", "")
        old = inp.get("old_string", "")
        old_preview = old[:40] + "..." if len(old) > 40 else old
        return f"{path}: {old_preview}"

    if name == "list_files":
        return inp.get("file_path", ".")

    if name == "grep_search":
        pattern = inp.get("pattern", "")
        path = inp.get("file_path") or "."
        include = inp.get("include")
        suffix = f" in {path}" if path != "." else ""
        filter_note = f" ({include})" if include else ""
        return f"pattern: {pattern}{suffix}{filter_note}"

    if name == "run_shell":
        cmd = inp.get("command", "")
        return cmd[:60] + "..." if len(cmd) > 60 else cmd

    if name == "web_fetch":
        url = inp.get("url", "")
        max_len = inp.get("max_length")
        suffix = f" (max {max_len})" if max_len else ""
        return f"{url}{suffix}"

    if name == "tool_search":
        return f"query: {inp.get('query', '')}"

    if name == "enter_plan_mode":
        return "switching to plan mode"

    keys = list(inp.keys())
    return f"args: {', '.join(keys)}" if keys else ""


# 在控制台打印工具调用（图标 + 名称 + 摘要）
def print_tool_call(name: str, inp: dict) -> None:
    icon = _get_tool_icon(name)
    summary = _get_tool_summary(name, inp)
    console.print(f"\n  [yellow]{icon} {name}[/yellow][dim] {summary}[/dim]")


# 在控制台打印工具执行结果（截断超过 500 字符的输出）
def print_tool_result(name: str, result: str) -> None:
    max_len = 500
    truncated = result[:max_len] + f"\n  ... ({len(result)} chars total)" if len(result) > max_len else result
    lines = "\n".join("  " + l for l in truncated.split("\n"))
    console.print(f"[dim]{lines}[/dim]")

def print_plan_for_approval(plan_content: str) -> None:
    console.print("\n[cyan]━━━ Plan for Approval ━━━[/cyan]")
    lines = plan_content.split("\n")
    max_lines = 60
    display = lines[:max_lines]
    for line in display:
        console.print(f"  [white]{line}[/white]")
    if len(lines) > max_lines:
        console.print(f"  [dim]... ({len(lines) - max_lines} more lines)[/dim]")
    console.print("  [cyan]━━━━━━━━━━━━━━━━━━━━━━━━[/cyan]\n")


def print_plan_approval_options() -> None:
    console.print("  [yellow]Choose an option:[/yellow]")
    console.print("    1) Yes, clear context and execute — fresh start with auto-accept edits")
    console.print("    2) Yes, and execute — keep context, auto-accept edits")
    console.print("    3) Yes, manually approve edits — keep context, confirm each edit")
    console.print("    4) No, keep planning — provide feedback to revise")


def print_sub_agent_start(agent_type: str, description: str) -> None:
    console.print(f"\n  [magenta]┌─ Sub-agent [{agent_type}]: {description}[/magenta]")

def print_sub_agent_end(agent_type: str, _description: str) -> None:
    console.print(f"  [magenta]└─ Sub-agent [{agent_type}] completed[/magenta]")