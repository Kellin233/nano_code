"""Active skill 生命周期管理。

本模块记录当前会话中已经激活、并且 compact 后仍需要继续生效的 skills。
它负责按最近使用顺序保留有限数量的 skill，并按 token 预算构建可重挂的上下文块。
"""

from __future__ import annotations

import time

from .types import ActiveSkill, SkillInvocationResult


def _approx_tokens(text: str) -> int:
    """用字符数粗略估算 token 数，满足上下文预算裁剪即可。"""
    return max(1, len(text) // 4) if text else 0


def _truncate_to_tokens(text: str, token_budget: int) -> str:
    """按粗略 token 预算截断文本，并追加省略提示。"""
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
        """初始化 active skill 数量和上下文预算限制。"""
        self.max_active = max_active
        self.per_skill_token_budget = per_skill_token_budget
        self.total_token_budget = total_token_budget
        self._skills: dict[str, ActiveSkill] = {}

    def record(self, invocation: SkillInvocationResult) -> None:
        """记录一次成功调用的 skill，并刷新最近使用时间。"""
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
            disallowed_tools=invocation.disallowed_tools,
            last_used_at=time.time(),
            approx_token_count=_approx_tokens(invocation.rendered_prompt),
        )
        self._skills[skill.name] = active
        self._trim()

    def clear(self) -> None:
        """清空当前会话记录的 active skills。"""
        self._skills.clear()

    def list_active(self) -> list[ActiveSkill]:
        """按最近使用时间倒序返回 active skills。"""
        return sorted(self._skills.values(), key=lambda s: s.last_used_at, reverse=True)

    def build_context(self) -> str:
        """构建 compact 后重新注入用户消息的 active skill 上下文。"""
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
                "\n".join(
                    [
                        f"[Active skill: {skill.name}]",
                        f"Invoked by: {skill.invoked_by}",
                        f"Context: {skill.context}",
                        f"Arguments: {skill.args or '(none)'}",
                        "",
                        prompt,
                    ]
                )
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
        """汇总所有 active skills 禁用的工具名。"""
        denied: set[str] = set()
        for skill in self._skills.values():
            if skill.disallowed_tools:
                denied.update(skill.disallowed_tools)
        return denied

    def _trim(self) -> None:
        """只保留最近使用的 `max_active` 个 skills。"""
        active = self.list_active()
        for stale in active[self.max_active:]:
            self._skills.pop(stale.name, None)
