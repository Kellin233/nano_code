"""Skill prompt 相关的兼容函数和描述生成。

本模块负责把 discovery 阶段得到的 skill metadata 渲染成 system prompt 片段，
同时提供旧调用路径需要的 prompt 渲染和执行辅助函数。
"""

from __future__ import annotations

from .runtime import SkillInvocation
from .registry import discover_skills, get_default_registry
from .types import SkillDefinition


def resolve_skill_prompt(skill: SkillDefinition, args: str) -> str:
    """渲染单个 skill 的 prompt，供兼容调用方直接使用。"""
    return SkillInvocation(get_default_registry()).render_prompt(skill, args)


def execute_skill(skill_name: str, args: str, invoked_by: str = "model") -> dict | None:
    """通过默认 registry 调用 skill，并返回旧格式的字典结果。"""
    invocation = SkillInvocation(get_default_registry()).invoke(skill_name, args, invoked_by)
    if not invocation.ok:
        return None
    return {
        "prompt": invocation.rendered_prompt,
        "allowed_tools": invocation.allowed_tools,
        "disallowed_tools": invocation.disallowed_tools,
        "context": invocation.context,
        "agent": invocation.agent,
        "skill": invocation.skill,
        "invocation": invocation,
    }


def _format_skill_invocation_modes(skill: SkillDefinition) -> str:
    """把一个 skill 的用户/模型调用方式格式化成简短文本。"""
    modes: list[str] = []
    if skill.user_invocable:
        hint = f" {skill.argument_hint}" if skill.argument_hint else ""
        modes.append(f"user=/{skill.name}{hint}")
    if not skill.disable_model_invocation:
        modes.append("model=skill tool")
    return ", ".join(modes) if modes else "disabled"


def build_skill_descriptions() -> str:
    """构建注入 system prompt 的 skill metadata 描述。"""
    skills = discover_skills()
    if not skills:
        return ""

    visible_skills = [
        skill for skill in skills
        if skill.user_invocable or not skill.disable_model_invocation
    ]
    if not visible_skills:
        return ""

    lines = [
        "# Available Skills",
        "",
        "Three-stage disclosure: this section contains metadata only. Full SKILL.md content is loaded only after invoking a skill. Supporting files under ${CLAUDE_SKILL_DIR} are never preloaded; read them on demand with read_file.",
        "",
        "Skills:",
    ]

    for skill in visible_skills:
        description = skill.description or "(no description)"
        modes = _format_skill_invocation_modes(skill)
        lines.append(
            f"- **{skill.name}** [{skill.context}] invoke: {modes}; {description}"
        )
        if skill.when_to_use:
            lines.append(f"  When to use: {skill.when_to_use}")

    lines.append("")
    lines.append("When you need a model-invocable skill, use the `skill` tool with the skill name and optional arguments.")
    return "\n".join(lines)
