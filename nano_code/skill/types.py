"""Skill 运行时的数据结构定义。

本模块只描述 skill 运行时在各阶段传递的数据，不包含扫描、渲染或执行逻辑。
这些类型被 registry、invocation、active manager 和 Agent 共同使用。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SkillDefinition:
    """描述一个可用 skill 的元数据和磁盘位置。"""

    name: str
    description: str
    when_to_use: str | None = None
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    user_invocable: bool = True
    disable_model_invocation: bool = False
    context: str = "inline"  # "inline" or "fork"
    agent: str | None = None
    argument_hint: str | None = None
    prompt_template: str = ""
    source: str = "project"  # "project" or "user"
    skill_dir: str = ""
    path: str = ""


@dataclass
class SkillInvocationResult:
    """保存一次 skill 调用后的规范化结果。"""

    skill: SkillDefinition | None
    args: str = ""
    invoked_by: str = "model"  # "user" or "model"
    rendered_prompt: str = ""
    context: str = "inline"
    agent: str | None = None
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        """判断本次调用是否成功取得 skill 和渲染结果。"""
        return self.error is None and self.skill is not None


@dataclass
class ActiveSkill:
    """记录当前会话中仍需要持续生效的 skill。"""

    name: str
    source: str
    skill_dir: str
    context: str
    rendered_prompt: str
    args: str
    invoked_by: str
    disallowed_tools: list[str] | None
    last_used_at: float
    approx_token_count: int
