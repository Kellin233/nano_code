# 计划模式

## 概述

nanocode 的"计划模式"通过 `plan` 子 Agent 类型实现——一个只读的 Agent，专门分析代码库并输出结构化规划。它不修改任何文件，只产出计划供主 Agent（或用户）审阅后执行。

## 实现方式

`plan` 是 subagent 系统的一个内置类型，定义在 `capabilities/subagents/__init__.py`：

```python
PLAN_PROMPT = """You are a Plan agent — a READ-ONLY sub-agent 
specialized for designing implementation plans.

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

# plan Agent 只有 3 个只读工具
plan_config = {"system_prompt": PLAN_PROMPT, "tools": [read_file, list_files, grep_search]}
```

## 工作流程

```
用户: "实现一个用户认证系统"

主 Agent 调用 agent(type="plan", prompt="设计认证系统的实现方案")
    │
    ├── SubAgentOrchestrator 派发 plan 子 Agent
    ├── plan Agent 拥有 read_file, list_files, grep_search
    │     → 研究现有代码结构
    │     → 分析需要改动的文件
    │     → 输出步骤化方案 + 风险考量
    │
    └── 主 Agent 拿到 plan 的文本输出
        → 展示给用户确认
        → 按计划逐步执行
```

## 与 Claude Code Plan Mode 的区别

Claude Code 的 Plan Mode 是**系统级功能**——Agent 进入"先规划再执行"模式，切换了整体行为。nanocode 的 `plan` 子 Agent 是**工具级功能**——它只是一个带特殊提示词的只读子 Agent。

| | Claude Code Plan Mode | nanocode plan subagent |
|---|---|---|
| 级别 | 系统级模式切换 | 子 Agent 类型 |
| 触发 | 用户或模型主动进入 | 模型调用 agent 工具 |
| 计划执行 | 系统确保"计划确认后才执行" | 依赖模型自觉性 |
| 读写控制 | 全局只读模式 | plan Agent 本身只读，主 Agent 不受限 |

**当前局限**：plan 子 Agent 输出的计划是否被执行完全取决于主 Agent 的自觉性。后续可以加系统级 Plan Mode（`AgentMode.execute` / `AgentMode.plan`），让 Agent 进入全局只读模式，计划被用户确认后才能切回执行模式。这个功能在 subagent 设计文档中被列为 Stage 4，不属于当前迭代。

## 面试考点

**Q: 为什么让模型自己决定何时用 plan，而不是强制？**

因为不是所有任务都需要计划——问"这个函数是做什么的"只需要一个 read_file + 一句话回答。强制计划会浪费 token。让模型根据任务复杂度自主选择是否用 plan 子 Agent，是更高效的交互模式。系统级 Plan Mode（用户强制规划）和工具级 plan Agent（模型自主选择）是两个互补的设计。
