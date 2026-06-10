"""Skill 软件包的统一入口。

本包负责 Claude Code 风格 skill 的发现、调用、上下文重挂和 system prompt 描述。
外部模块优先从这里导入，避免依赖包内具体文件结构。
"""

from .prompt import build_skill_descriptions, execute_skill, resolve_skill_prompt
from .registry import (
    SkillRegistry,
    discover_skills,
    get_default_registry,
    get_skill_by_name,
    reset_skill_cache,
)
from .runtime import ActiveSkillManager, SkillInvocation
from .types import ActiveSkill, SkillDefinition, SkillInvocationResult

__all__ = [
    "ActiveSkill",
    "ActiveSkillManager",
    "SkillDefinition",
    "SkillInvocation",
    "SkillInvocationResult",
    "SkillRegistry",
    "build_skill_descriptions",
    "discover_skills",
    "execute_skill",
    "get_default_registry",
    "get_skill_by_name",
    "reset_skill_cache",
    "resolve_skill_prompt",
]
