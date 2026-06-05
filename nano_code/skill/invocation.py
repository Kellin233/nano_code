"""Skill 调用和 prompt 渲染。

本模块负责统一处理用户调用和模型调用，包括调用权限检查、`SKILL.md` 正文懒加载、
参数占位符替换，以及 `${CLAUDE_SKILL_DIR}` 变量注入。
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from ..frontmatter import parse_frontmatter
from .registry import get_default_registry, SkillRegistry
from .types import SkillDefinition, SkillInvocationResult


def _split_args(args: str) -> list[str]:
    """按 shell 风格拆分参数；解析失败时退回简单空白拆分。"""
    if not args:
        return []
    try:
        return shlex.split(args)
    except ValueError:
        return args.split()


def _load_skill_body(skill: SkillDefinition) -> str:
    """懒加载 skill 正文，兼容手动构造的 `prompt_template`。"""
    if skill.path:
        raw = Path(skill.path).read_text(encoding="utf-8")
        return parse_frontmatter(raw).body
    return skill.prompt_template


class SkillInvocation:
    """统一执行 skill 调用前检查和 prompt 渲染。"""

    def __init__(self, registry: SkillRegistry | None = None):
        """绑定 registry；未传入时使用默认 registry。"""
        self.registry = registry or get_default_registry()

    def invoke(
        self, skill_name: str, args: str = "", invoked_by: str = "model"
    ) -> SkillInvocationResult:
        """调用指定 skill，返回成功结果或带错误信息的结果。"""
        skill = self.registry.get(skill_name)
        if not skill:
            return SkillInvocationResult(
                skill=None,
                args=args,
                invoked_by=invoked_by,
                error=f"Unknown skill: {skill_name}",
            )

        if invoked_by == "user" and not skill.user_invocable:
            return SkillInvocationResult(
                skill=skill,
                args=args,
                invoked_by=invoked_by,
                error=f'Skill "{skill.name}" is not user-invocable.',
            )

        if invoked_by == "model" and skill.disable_model_invocation:
            return SkillInvocationResult(
                skill=skill,
                args=args,
                invoked_by=invoked_by,
                error=f'Skill "{skill.name}" cannot be invoked by the model.',
            )

        try:
            rendered = self.render_prompt(skill, args)
        except Exception as exc:
            return SkillInvocationResult(
                skill=skill,
                args=args,
                invoked_by=invoked_by,
                error=f'Failed to load skill "{skill.name}": {exc}',
            )

        return SkillInvocationResult(
            skill=skill,
            args=args,
            invoked_by=invoked_by,
            rendered_prompt=rendered,
            context=skill.context,
            agent=skill.agent,
            allowed_tools=skill.allowed_tools,
            disallowed_tools=skill.disallowed_tools,
        )

    def render_prompt(self, skill: SkillDefinition, args: str) -> str:
        """渲染 skill 正文中的参数占位符和 skill 目录变量。"""
        prompt = _load_skill_body(skill)
        parts = _split_args(args)
        used_arg_placeholder = "$ARGUMENTS" in prompt or "${ARGUMENTS}" in prompt

        def replace_index(match: re.Match) -> str:
            """替换 `$0` 或 `$ARGUMENTS[0]` 这类位置参数占位符。"""
            nonlocal used_arg_placeholder
            used_arg_placeholder = True
            idx = int(match.group(1))
            return parts[idx] if idx < len(parts) else ""

        prompt = re.sub(r"\$ARGUMENTS\[(\d+)\]", replace_index, prompt)
        prompt = prompt.replace("${ARGUMENTS}", args)
        prompt = prompt.replace("$ARGUMENTS", args)
        prompt = re.sub(r"\$(\d+)\b", replace_index, prompt)
        prompt = prompt.replace("${CLAUDE_SKILL_DIR}", skill.skill_dir)

        if args and not used_arg_placeholder:
            prompt = prompt.rstrip() + f"\n\nARGUMENTS:\n{args}"

        return prompt
