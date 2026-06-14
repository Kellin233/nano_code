"""Skill 运行时：调用 + 激活状态管理。

合并了原 invocation.py（Skill 调用和 prompt 渲染）和
active.py（Active skill 生命周期管理）。

变更原因：
  - 改 skill 调用逻辑 → 改 SkillInvocation 类
  - 改激活状态管理 → 改 ActiveSkillManager 类
  - 两者常常一起变更（调用后必然记录激活状态，compact 后必然 reattach）
"""

from __future__ import annotations

import re
import shlex
import time
from pathlib import Path

from ....agent.harness.context.sources import parse_frontmatter
from .registry import SkillRegistry, get_default_registry
from .types import ActiveSkill, SkillDefinition, SkillInvocationResult

# ─── Skill 调用 ──────────────────────────────────


def _split_args(args: str) -> list[str]:
    """按 shell 风格拆分参数。"""
    if not args:
        return []
    try:
        return shlex.split(args)
    except ValueError:
        return args.split()


def _load_skill_body(skill: SkillDefinition) -> str:
    """懒加载 skill 正文。"""
    if skill.path:
        raw = Path(skill.path).read_text(encoding="utf-8")
        return parse_frontmatter(raw).body
    return skill.prompt_template


class SkillInvocation:
    """统一执行 skill 调用前检查和 prompt 渲染。"""

    def __init__(self, registry: SkillRegistry | None = None):
        self.registry = registry or get_default_registry()

    def invoke(
        self, skill_name: str, args: str = "", invoked_by: str = "model"
    ) -> SkillInvocationResult:
        skill = self.registry.get(skill_name)
        if not skill:
            return SkillInvocationResult(
                skill=None, args=args, invoked_by=invoked_by,
                error=f"Unknown skill: {skill_name}",
            )

        if invoked_by == "user" and not skill.user_invocable:
            return SkillInvocationResult(
                skill=skill, args=args, invoked_by=invoked_by,
                error=f'Skill "{skill.name}" is not user-invocable.',
            )

        if invoked_by == "model" and skill.disable_model_invocation:
            return SkillInvocationResult(
                skill=skill, args=args, invoked_by=invoked_by,
                error=f'Skill "{skill.name}" cannot be invoked by the model.',
            )

        try:
            rendered = self.render_prompt(skill, args)
        except Exception as exc:
            return SkillInvocationResult(
                skill=skill, args=args, invoked_by=invoked_by,
                error=f'Failed to load skill "{skill.name}": {exc}',
            )

        return SkillInvocationResult(
            skill=skill, args=args, invoked_by=invoked_by,
            rendered_prompt=rendered, context=skill.context,
            agent=skill.agent, allowed_tools=skill.allowed_tools,
            disallowed_tools=skill.disallowed_tools,
        )

    def render_prompt(self, skill: SkillDefinition, args: str) -> str:
        prompt = _load_skill_body(skill)
        parts = _split_args(args)
        used_arg = "$ARGUMENTS" in prompt or "${ARGUMENTS}" in prompt

        def replace_index(match: re.Match) -> str:
            nonlocal used_arg
            used_arg = True
            idx = int(match.group(1))
            return parts[idx] if idx < len(parts) else ""

        prompt = re.sub(r"\$ARGUMENTS\[(\d+)\]", replace_index, prompt)
        prompt = prompt.replace("${ARGUMENTS}", args)
        prompt = prompt.replace("$ARGUMENTS", args)
        prompt = re.sub(r"\$(\d+)\b", replace_index, prompt)
        prompt = prompt.replace("${CLAUDE_SKILL_DIR}", skill.skill_dir)

        if args and not used_arg:
            prompt = prompt.rstrip() + f"\n\nARGUMENTS:\n{args}"

        return prompt


# ─── Active Skill 管理 ────────────────────────────


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4) if text else 0


def _truncate_to_tokens(text: str, token_budget: int) -> str:
    char_budget = max(0, token_budget * 4)
    if len(text) <= char_budget:
        return text
    omitted = len(text) - char_budget
    return text[:char_budget] + f"\n\n[... active skill truncated, {omitted} chars omitted ...]"


class ActiveSkillManager:
    """维护当前会话里仍需持续生效的 skills。"""

    def __init__(
        self,
        *,
        max_active: int = 8,
        per_skill_token_budget: int = 5000,
        total_token_budget: int = 25000,
    ):
        self.max_active = max_active
        self.per_skill_token_budget = per_skill_token_budget
        self.total_token_budget = total_token_budget
        self._skills: dict[str, ActiveSkill] = {}

    def record(self, invocation: SkillInvocationResult) -> None:
        if not invocation.ok or not invocation.skill:
            return
        skill = invocation.skill
        active = ActiveSkill(
            name=skill.name,
            source=skill.source,
            skill_dir=skill.skill_dir,
            context=invocation.context,
            rendered_prompt=invocation.rendered_prompt,
            args=invocation.args,
            invoked_by=invocation.invoked_by,
            allowed_tools=invocation.allowed_tools,
            disallowed_tools=invocation.disallowed_tools,
            last_used_at=time.time(),
            approx_token_count=_approx_tokens(invocation.rendered_prompt),
        )
        self._skills[skill.name] = active
        self._trim()

    def clear(self) -> None:
        self._skills.clear()

    def list_active(self) -> list[ActiveSkill]:
        return sorted(self._skills.values(), key=lambda s: s.last_used_at, reverse=True)

    def build_context(self) -> str:
        blocks: list[str] = []
        used_tokens = 0
        for skill in self.list_active():
            budget_left = self.total_token_budget - used_tokens
            if budget_left <= 0:
                break
            budget = min(self.per_skill_token_budget, budget_left)
            prompt = _truncate_to_tokens(skill.rendered_prompt, budget)
            prompt_tokens = _approx_tokens(prompt)
            if used_tokens + prompt_tokens > self.total_token_budget:
                break
            used_tokens += prompt_tokens
            blocks.append(
                "\n".join([
                    f"[Active skill: {skill.name}]",
                    f"Invoked by: {skill.invoked_by}",
                    f"Context: {skill.context}",
                    f"Arguments: {skill.args or '(none)'}",
                    "",
                    prompt,
                ])
            )
        if not blocks:
            return ""
        return (
            "[Active skills restored after context compaction]\n"
            "These skill instructions were previously invoked in this session. "
            "Continue following them when relevant.\n\n"
            + "\n\n---\n\n".join(blocks)
        )

    def disallowed_tools(self) -> set[str]:
        denied: set[str] = set()
        for skill in self._skills.values():
            if skill.disallowed_tools:
                denied.update(skill.disallowed_tools)
        return denied

    def allowed_tools(self) -> set[str] | None:
        allowed_sets = [
            set(skill.allowed_tools)
            for skill in self._skills.values()
            if skill.allowed_tools
        ]
        if not allowed_sets:
            return None
        allowed = allowed_sets[0]
        for item in allowed_sets[1:]:
            allowed &= item
        return allowed

    def _trim(self) -> None:
        active = self.list_active()
        for stale in active[self.max_active:]:
            self._skills.pop(stale.name, None)
