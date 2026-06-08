"""Dynamic context attachment rendering."""

from __future__ import annotations

from typing import Iterable


def render_system_reminder(title: str, body: str) -> str:
    body = (body or "").strip()
    if not body:
        return ""
    return f"<system-reminder>\n{title.strip()}\n\n{body}\n</system-reminder>"


def render_memory_attachment(memories: list[object]) -> str:
    if not memories:
        return ""
    lines = []
    for memory in memories:
        path = getattr(memory, "path", getattr(memory, "filename", "memory"))
        content = getattr(memory, "content", "")
        lines.append(f"Memory: {path}\n{content}")
    return render_system_reminder("Relevant long-term memory.", "\n\n".join(lines))


def render_skill_listing_attachment(skills: Iterable[object], sent: set[str]) -> tuple[str, set[str]]:
    visible = [
        skill for skill in skills
        if getattr(skill, "user_invocable", False) or not getattr(skill, "disable_model_invocation", False)
    ]
    new_skills = [skill for skill in visible if getattr(skill, "name", "") not in sent]
    if not new_skills:
        return "", set(sent)

    lines = [
        "Available skills. Metadata only; full SKILL.md content is loaded only after invoking a skill.",
        "",
        "Use the `skill` tool when a model-invocable skill is relevant.",
    ]
    updated = set(sent)
    for skill in new_skills:
        name = getattr(skill, "name", "")
        updated.add(name)
        description = getattr(skill, "description", "") or "(no description)"
        context = getattr(skill, "context", "main")
        modes: list[str] = []
        if getattr(skill, "user_invocable", False):
            hint = f" {getattr(skill, 'argument_hint', '')}" if getattr(skill, "argument_hint", "") else ""
            modes.append(f"user=/{name}{hint}")
        if not getattr(skill, "disable_model_invocation", False):
            modes.append("model=skill tool")
        lines.append(f"- {name} [{context}] invoke: {', '.join(modes) if modes else 'disabled'}; {description}")
        when_to_use = getattr(skill, "when_to_use", "")
        if when_to_use:
            lines.append(f"  When to use: {when_to_use}")
    return render_system_reminder("Skill listing update.", "\n".join(lines)), updated


def render_deferred_tools_attachment(names: list[str]) -> str:
    if not names:
        return ""
    lines = [
        "The following deferred tools are available through `tool_search`.",
        "Use `tool_search` to fetch full schemas when one is needed.",
        "",
        ", ".join(sorted(names)),
    ]
    return render_system_reminder("Deferred tool listing.", "\n".join(lines))


def render_mcp_delta_attachment(delta: object) -> str:
    added = sorted(getattr(delta, "added", []) or [])
    removed = sorted(getattr(delta, "removed", []) or [])
    changed = sorted(getattr(delta, "changed", []) or [])
    if not added and not removed and not changed:
        return ""
    lines = ["MCP tool list changed. Tool schemas in the registry will be visible on the next model request."]
    if added:
        lines.append("Added: " + ", ".join(added))
    if changed:
        lines.append("Changed: " + ", ".join(changed))
    if removed:
        lines.append("Removed: " + ", ".join(removed))
    return render_system_reminder("MCP tool update.", "\n".join(lines))
