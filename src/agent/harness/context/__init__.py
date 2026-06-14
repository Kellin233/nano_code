"""上下文构建模块 — system prompt + 启动上下文 + 数据源。"""

from .builder import (
    SYSTEM_PROMPT_DYNAMIC_BOUNDARY,
    build_prompt_bundle,
    build_stable_system_prompt,
    build_startup_context,
    build_system_prompt,
    render_deferred_tools_attachment,
    render_mcp_delta_attachment,
    render_skill_listing_attachment,
    render_system_reminder,
)
from .sources import (
    FrontmatterResult,
    GitContextResult,
    InstructionLoadResult,
    PromptBundle,
    PromptDiagnostic,
    collect_git_context,
    format_frontmatter,
    load_project_instructions,
    parse_frontmatter,
)

__all__ = [
    "FrontmatterResult",
    "GitContextResult",
    "InstructionLoadResult",
    "PromptBundle",
    "PromptDiagnostic",
    "SYSTEM_PROMPT_DYNAMIC_BOUNDARY",
    "build_prompt_bundle",
    "build_stable_system_prompt",
    "build_startup_context",
    "build_system_prompt",
    "collect_git_context",
    "format_frontmatter",
    "load_project_instructions",
    "parse_frontmatter",
    "render_deferred_tools_attachment",
    "render_mcp_delta_attachment",
    "render_skill_listing_attachment",
    "render_system_reminder",
]
