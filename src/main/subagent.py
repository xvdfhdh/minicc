from __future__ import annotations
from src.tools.tools import *
from src.memory.frontmatter import parse_frontmatter
from pathlib import Path
import json

READ_ONLY_TOOLS = {"read_file", "list_files", "grep_search"}

def _get_read_only_tools()->list[ToolDef]:
    return [t for t in tool_definitions if t["name"] in READ_ONLY_TOOLS]


_cached_custom_agents: dict | None = None


def _discover_custom_agents() -> dict[str, dict]:
    """从 ~/.minicc/agents/ 和 .minicc/agents/ 发现用户自定义子代理。
    返回 {agent_name: {system_prompt, allowed_tools}} 字典。
    项目级定义覆盖用户级同名定义。
    """
    global _cached_custom_agents
    if _cached_custom_agents is not None:
        return _cached_custom_agents

    agents: dict[str, dict] = {}
    search_dirs = [
        Path.home() / ".minicc" / "agents",
        Path.cwd() / ".minicc" / "agents",
    ]

    for agents_dir in search_dirs:
        if not agents_dir.is_dir():
            continue
        for f in sorted(agents_dir.glob("*.md")):
            try:
                content = f.read_text(encoding="utf-8")
                parsed = parse_frontmatter(content)
                meta = parsed.meta
                name = meta.get("name") or f.stem
                if not name:
                    continue

                allowed_tools: list[str] | None = None
                if "allowed_tools" in meta:
                    raw = meta["allowed_tools"]
                    if raw.startswith("["):
                        try:
                            allowed_tools = json.loads(raw)
                        except Exception:
                            allowed_tools = [s.strip() for s in raw.strip("[]").split(",")]
                    else:
                        allowed_tools = [s.strip() for s in raw.split(",")]

                agents[name] = {
                    "system_prompt": parsed.body,
                    "allowed_tools": allowed_tools,
                }
            except Exception:
                continue

    _cached_custom_agents = agents
    return agents

EXPLORE_PROMPT = """You are an Explore agent — a fast, READ-ONLY sub-agent specialized for codebase exploration.

IMPORTANT CONSTRAINTS:
- You are READ-ONLY. You only have access to read_file, list_files, and grep_search.
- Do NOT attempt to modify any files.

Be fast and thorough. Use multiple tool calls when possible. Return a concise summary of your findings."""

PLAN_PROMPT = """You are a Plan agent — a READ-ONLY sub-agent specialized for designing implementation plans.

Return a structured plan with:
1. Summary of current state
2. Step-by-step implementation steps
3. Critical files for implementation
4. Potential risks or considerations"""

GENERAL_PROMPT = "You are a General sub-agent handling an independent task. Complete the assigned task and return a concise result. You have access to all tools."

def get_sub_agent_config(agent_type: str) -> dict:
    custom = _discover_custom_agents().get(agent_type)
    if custom:
        if custom["allowed_tools"]:
            tools = [t for t in tool_definitions if t["name"] in custom["allowed_tools"]]
        else:
            tools = [t for t in tool_definitions if t["name"] != "agent"]
        return {"system_prompt": custom["system_prompt"], "tools": tools}

    read_only = [t for t in tool_definitions if t["name"] in READ_ONLY_TOOLS]
    if agent_type == "explore":
        return {"system_prompt": EXPLORE_PROMPT, "tools": read_only}
    elif agent_type == "plan":
        return {"system_prompt": PLAN_PROMPT, "tools": read_only}
    else:
        return {"system_prompt": GENERAL_PROMPT, "tools": [t for t in tool_definitions if t["name"] != "agent"]}