"""子智能体系统：分叉返回模式，支持内置和自定义智能体类型。
对应 Claude Code 的 AgentTool：explore（只读）、plan（结构化规划）、general（完整工具），
并支持通过 .claude/agents/*.md 定义用户自定义智能体。

并行编排见 orchestrator.py。
"""

from __future__ import annotations

import copy
from pathlib import Path

from ....agent.runtime_management.context.sources import parse_frontmatter
from ..tools.builtin import builtin_tool_definitions

# ─── 只读工具（供探索和规划智能体使用）──────────
# Explore / Plan 子智能体只拿到这三个工具的 schema。
# 这是一层硬限制：模型即使想调用 write_file、edit_file 或 run_shell，
# API 侧也看不到这些工具定义，无法产生合法工具调用。

READ_ONLY_TOOLS = {"read_file", "list_files", "grep_search"}

EXPLORE_PROMPT = """You are a file search specialist for Nano Code. You excel at thoroughly navigating and exploring codebases.

=== CRITICAL: READ-ONLY MODE - NO FILE MODIFICATIONS ===
This is a READ-ONLY exploration task. You are STRICTLY PROHIBITED from:
- Creating new files (no write_file, touch, or file creation of any kind)
- Modifying existing files (no edit_file operations)
- Deleting files (no rm or deletion)
- Running ANY commands that change system state

Your role is EXCLUSIVELY to search and analyze existing code.

Your strengths:
- Rapidly finding files using glob patterns
- Searching code and text with powerful regex patterns
- Reading and analyzing file contents

Guidelines:
- Use list_files for broad file pattern matching
- Use grep_search for searching file contents with regex
- Use read_file when you know the specific file path you need to read
- Adapt your search approach based on the thoroughness level specified by the caller

NOTE: You are meant to be a fast agent that returns output as quickly as possible. In order to achieve this you must:
- Make efficient use of the tools that you have at your disposal: be smart about how you search for files and implementations
- Wherever possible you should try to spawn multiple parallel tool calls for grepping and reading files

Complete the user's search request efficiently and report your findings clearly."""

PLAN_PROMPT = """You are a Plan agent — a READ-ONLY sub-agent specialized for designing implementation plans.

IMPORTANT CONSTRAINTS:
- You are READ-ONLY. You only have access to read_file, list_files, and grep_search.
- Do NOT attempt to modify any files.

Your job:
- Analyze the codebase to understand the current architecture
- Design a step-by-step implementation plan
- Identify critical files that need modification
- Consider architectural trade-offs

Return a structured plan with:
1. Summary of current state
2. Step-by-step implementation steps
3. Critical files for implementation
4. Potential risks or considerations"""

GENERAL_PROMPT = """You are an agent for Nano Code. Given the user's message, you should use the tools available to complete the task. Complete the task fully—don't gold-plate, but don't leave it half-done. When you complete the task, respond with a concise report covering what was done and any key findings — the caller will relay this to the user, so it only needs the essentials.

Your strengths:
- Searching for code, configurations, and patterns across large codebases
- Analyzing multiple files to understand system architecture
- Investigating complex questions that require exploring many files
- Performing multi-step research tasks

Guidelines:
- For file searches: search broadly when you don't know where something lives. Use read_file when you know the specific file path.
- For analysis: Start broad and narrow down. Use multiple search strategies if the first doesn't yield results.
- Be thorough: Check multiple locations, consider different naming conventions, look for related files.
- NEVER create files unless they're absolutely necessary for achieving your goal. ALWAYS prefer editing an existing file to creating a new one."""

# ─── 自定义智能体发现 ─────────────────────────────────

_cached_custom_agents: dict[str, dict] | None = None


def _discover_custom_agents() -> dict[str, dict]:
    """发现用户级和项目级自定义智能体，并缓存解析结果。"""
    global _cached_custom_agents
    if _cached_custom_agents is not None:
        return _cached_custom_agents

    agents: dict[str, dict] = {}
    # 用户级（优先级较低）
    _load_agents_from_dir(Path.home() / ".claude" / "agents", agents)
    # 项目级（优先级较高，可覆盖）
    _load_agents_from_dir(Path.cwd() / ".claude" / "agents", agents)

    _cached_custom_agents = agents
    return agents


def _load_agents_from_dir(directory: Path, agents: dict[str, dict]) -> None:
    """加载一个 .claude/agents 目录，后加载的同名智能体会覆盖先加载的。"""
    if not directory.is_dir():
        return
    for entry in directory.iterdir():
        if entry.suffix != ".md":
            continue
        try:
            raw = entry.read_text()
            result = parse_frontmatter(raw)
            meta = result.meta
            name = meta.get("name") or entry.stem
            allowed_tools = None
            if "allowed-tools" in meta:
                # allowed-tools 是白名单；未声明时稍后会给完整工具集但排除 agent，
                # 防止自定义智能体继续递归创建子智能体。
                allowed_tools = [s.strip() for s in meta["allowed-tools"].split(",")]
            agents[name] = {
                "name": name,
                "description": meta.get("description", ""),
                "allowed_tools": allowed_tools,
                "system_prompt": result.body,
            }
        except Exception:
            pass


# ─── 主配置函数 ───────────────────────────────────


def get_sub_agent_config(agent_type: str) -> dict:
    """返回指定智能体类型对应的配置字典。"""
    tools = builtin_tool_definitions()
    custom = _discover_custom_agents().get(agent_type)
    if custom:
        if custom["allowed_tools"]:
            # 自定义智能体声明 allowed-tools 时严格按白名单过滤。
            selected = [t for t in tools if t["name"] in custom["allowed_tools"]]
        else:
            # 未声明白名单时给它普通工具，但仍排除 agent，避免 A→B→C 的递归派生。
            selected = [t for t in tools if t["name"] != "agent"]
        return {"system_prompt": custom["system_prompt"], "tools": selected}

    read_only = [t for t in tools if t["name"] in READ_ONLY_TOOLS]

    if agent_type == "explore":
        return {"system_prompt": EXPLORE_PROMPT, "tools": read_only}
    elif agent_type == "plan":
        return {"system_prompt": PLAN_PROMPT, "tools": read_only}
    else:  # 通用类型
        # 未知类型回退到 general：最大化可用性，但仍禁止子智能体再创建子智能体。
        return {"system_prompt": GENERAL_PROMPT, "tools": [t for t in tools if t["name"] != "agent"]}


# ─── 可用智能体类型（用于系统提示词）──────────────


def get_available_agent_types() -> list[dict[str, str]]:
    types = [
        {"name": "explore", "description": "Fast, read-only codebase search and exploration"},
        {"name": "plan", "description": "Read-only analysis with structured implementation plans"},
        {"name": "general", "description": "Full tools for independent tasks"},
    ]
    for name, defn in _discover_custom_agents().items():
        types.append({"name": name, "description": defn["description"]})
    return types


def build_agent_descriptions() -> str:
    """把自定义智能体类型追加进主系统提示词，供模型选择 agent 类型。"""
    types = get_available_agent_types()
    if len(types) <= 3:
        return ""  # 只有内置类型时，它们已写入系统提示词。

    custom = types[3:]
    lines = ["\n# Custom Agent Types", ""]
    for t in custom:
        lines.append(f"- **{t['name']}**: {t['description']}")
    return "\n".join(lines)


def build_agent_tool_definition(base: dict) -> dict:
    """Return an agent tool schema with current built-in and custom agent types."""
    tool = copy.deepcopy(base)
    types = get_available_agent_types()
    names = [item["name"] for item in types]
    properties = tool.get("input_schema", {}).get("properties", {})
    type_schema = properties.get("type")
    if isinstance(type_schema, dict):
        type_schema["enum"] = names
    tasks_schema = properties.get("tasks")
    task_type_schema = (
        tasks_schema.get("items", {})
        .get("properties", {})
        .get("type")
        if isinstance(tasks_schema, dict)
        else None
    )
    if isinstance(task_type_schema, dict):
        task_type_schema["enum"] = names

    custom = types[3:]
    if custom:
        listing = "; ".join(f"{item['name']}: {item['description']}" for item in custom)
        tool["description"] = f"{tool.get('description', '')}\nAvailable custom agent types: {listing}"
    return tool


def reset_agent_cache() -> None:
    global _cached_custom_agents
    _cached_custom_agents = None
