

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import json
import re
from src.memory.frontmatter import parse_frontmatter


@dataclass
class SkillDefinition:
    """技能定义：描述一个可由用户或 Agent 调用的技能模板"""
    name: str
    description: str
    when_to_use: str | None
    allowed_tools: list[str] | None
    user_invocable: bool
    context: str               # "fork" | "inline"
    prompt_template: str
    source: str                # "user" | "project"
    skill_dir: str             # 技能文件所在目录


_cached_skills: list[SkillDefinition] | None = None


def _load_skills_from_dir(dir_path: Path, source: str, skills: dict[str, SkillDefinition]) -> None:
    """从目录加载技能定义文件（.md with YAML frontmatter）"""
    if not dir_path.is_dir():
        return
    for f in sorted(dir_path.glob("*.md")):
        parsed = _parse_skill_file(f, source, str(dir_path))
        if parsed:
            skills[parsed.name] = parsed



def discover_skills()->list[SkillDefinition]:
    global _cached_skills
    if _cached_skills is not None:
        return _cached_skills

    skills: dict[str, SkillDefinition] = {}

    _load_skills_from_dir(Path.home() / ".minicc" / "skills", "user", skills)
    _load_skills_from_dir(Path.cwd() / ".minicc" / "skills", "project", skills)

    _cached_skills = list(skills.values())
    return _cached_skills

def _parse_skill_file(
    file_path: Path, source: str, skill_dir: str
) -> SkillDefinition | None:
    try:
        raw=file_path.read_text(encoding="utf-8")
        result=parse_frontmatter(raw)
        meta=result.meta

        name=meta.get("name") or file_path.parent.name or "unknown"
        user_invocable=meta.get("user_invocable", True)!="false"
        context="fork" if meta.get("context")=="fork" else "inline"

        allowed_tools: list[str] |None=None
        if "allowed_tools" in meta:
            raw_tools=meta["allowed_tools"]
            if raw_tools.startswith("["):
                try:
                    allowed_tools=json.loads(raw_tools)
                except Exception:
                    allowed_tools=[s.strip() for s in raw_tools.strip("[]").split(",")]
            else:
                allowed_tools=[s.strip() for s in raw_tools.split(",")]

        return SkillDefinition(
            name=name, description=meta.get("description", ""),
            when_to_use=meta.get("when_to_use") or meta.get("when-to-use"),
            allowed_tools=allowed_tools, user_invocable=user_invocable,
            context=context, prompt_template=result.body,
            source=source, skill_dir=skill_dir,
        )
    except Exception:
        return None

def resolve_skill_prompt(skill: SkillDefinition,args:str) -> str:
    prompt = re.sub(r"\$ARGUMENTS|\$\{ARGUMENTS\}", args, skill.prompt_template)
    prompt = prompt.replace("${CLAUDE_SKILL_DIR}", skill.skill_dir)
    return prompt

def execute_skill(name: str, args: str) -> dict | None:
    """按名称查找并解析技能，返回 prompt/context/allowed_tools 字典"""
    for skill in discover_skills():
        if skill.name == name and skill.user_invocable:
            resolved = resolve_skill_prompt(skill, args)
            return {
                "prompt": resolved,
                "name": name,
                "context": skill.context,
                "allowed_tools": skill.allowed_tools,
            }
    return None

def get_skill_by_name(name: str) -> SkillDefinition | None:
    """按名称查找 SkillDefinition"""
    for skill in discover_skills():
        if skill.name == name:
            return skill
    return None

async def _execute_skill_tool(inp: dict) -> str:
    result = execute_skill(inp.get("skill_name", ""), inp.get("args", ""))
    if not result:
        return f"Unknown skill: {inp.get('skill_name', '')}"
    return f'[Skill "{inp.get("skill_name", "")}" activated]\n\n{result["prompt"]}'

def build_skill_description()->str:
    skills=discover_skills()
    if not skills:
        return ""

    lines=["# Available Skills",""]
    invocable=[s for s in skills if s.user_invocable]
    auto_only=[s for s in skills if not s.user_invocable]

    if invocable:
        lines.append("User-invocable skills (user types /<name> to invoke):")
        for s in invocable:
            lines.append(f"- **/{s.name}**: {s.description}")
            if s.when_to_use:
                lines.append(f"  When to use: {s.when_to_use}")
        lines.append("")

    if auto_only:
        lines.append("Auto-invocable skills (use the skill tool when appropriate):")
        for s in auto_only:
            lines.append(f"- **{s.name}**: {s.description}")
            if s.when_to_use:
                lines.append(f"  When to use: {s.when_to_use}")
        lines.append("")

    lines.append("To invoke a skill programmatically, use the `skill` tool.")
    return "\n".join(lines)
