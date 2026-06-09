# 子 Agent 与计划模式

## 1. 为什么需要子 Agent

主 Agent 的上下文窗口有限。让它同时搜索代码、审查安全、跑测试——消息历史被中间产物填满，模型注意力被稀释。Codex CLI 把这个问题叫做"context pollution"——探索日志、测试输出、堆栈跟踪在主会话里堆积，模型质量下降。

子 Agent 解决这个问题：**把脏活累活 fork 到独立上下文，只把结果摘要带回来**。Codex CLI 的 multi-agent 文档称之为"context isolation as architecture"——架构层面的上下文隔离。

## 2. 核心概念

### 2.1 Fork-and-Return

```
主 Agent 调用 agent 工具
    │
    ├── get_sub_agent_config(type)
    │     ├── 查自定义 .md（.claude/agents/*.md）
    │     └── fallback 内置类型（explore/plan/general）
    │     → 返回 {"system_prompt": str, "tools": list}
    │
    ├── Agent(RuntimeConfig(is_sub_agent=True, custom_system_prompt=...),
    │         custom_tools=sub_config["tools"],
    │         sandbox_manager=parent._sandbox_manager)
    │
    └── agent.run_once(prompt)
          → 独立消息历史，独立 AgentLoop
          → 返回 {"text": str, "tokens": {"input": int, "output": int}}
```

关键点：子 Agent 和主 Agent 是**同一个 Agent 类**的不同实例。不是子类、不是特殊构造——通过 `custom_tools` 限制工具白名单，通过 `is_sub_agent=True` 跳过启动上下文和记忆系统。

### 2.2 三种内置类型

| 类型 | 工具白名单 | 用途 | 典型 prompt |
|------|-----------|------|------------|
| explore | read_file, list_files, grep_search | 搜索代码、探索项目、找到匹配 | "找到所有使用 Redis 的地方" |
| plan | 同上 3 个只读工具 | 分析架构、拆解任务、识别风险 | "设计用户认证系统的实现方案" |
| general | 全工具 - agent | 独立完成完整任务 | "修复 agent.py 的 bug 并跑测试" |

explore 和 plan 的工具白名单完全相同——只有 3 个只读工具。差异纯粹由 system prompt 驱动。explore 强调"快速、并行、返回结果"，plan 强调"分析架构、列出步骤、考虑 trade-off、识别风险"。

### 2.3 并行编排（SubAgentOrchestrator）

`SubAgentOrchestrator.dispatch(tasks)` 接收任务列表：

```python
tasks = [
    {"type": "explore", "prompt": "搜索迁移代码", "timeout": 30, "max_turns": 10},
    {"type": "explore", "prompt": "搜索路由定义", "timeout": 30, "max_turns": 10},
]
results = await orchestrator.dispatch(tasks)
```

`asyncio.gather` 并行执行，`Semaphore` 控制最大并发（默认 4），`asyncio.wait_for` 控制单任务超时（默认 60s）。超时后 `agent.abort()` 终止循环、释放 API 连接。一个子 Agent 失败不影响其他——`_execute_task()` 内部 try/except 捕获。

### 2.4 自定义 Agent

`.claude/agents/*.md` 定义，YAML frontmatter：

```yaml
---
name: code-reviewer
description: 审查代码变更
allowed-tools: read_file, grep_search, list_files, run_shell
---
... system prompt body ...
```

项目级覆盖用户级。`allowed-tools` 白名单约束。未声明时给全工具但排除 agent（防止递归）。全局缓存——`reset_agent_cache()` 清除。

### 2.5 计划模式（plan subagent vs Plan Mode）

当前 `plan` 子 Agent 是**工具级功能**——模型调用 `agent(type="plan")` 生成计划。输出是纯文本——主 Agent 拿到后是否按计划执行取决于模型自觉。这和 Claude Code 的系统级 Plan Mode（切换 Agent 全局行为为"先规划再执行"，确认后才允许修改文件）是两回事。系统级 Plan Mode 在 roadmap 中（P1）。

## 3. 总体设计

```
capabilities/subagents/
├── __init__.py        # 3 种内置类型 + 自定义发现 + get_sub_agent_config()
└── orchestrator.py    # SubAgentOrchestrator 并行编排器（~90 行）
```

## 4. 详细设计

**`__init__.py`**：`EXPLORE_PROMPT`/`PLAN_PROMPT`/`GENERAL_PROMPT`——三个内置 system prompt。`READ_ONLY_TOOLS = {"read_file", "list_files", "grep_search"}`。`get_sub_agent_config(type)` 是核心查找函数——先查自定义 `.md`，再 fallback 内置类型。`_discover_custom_agents()` 扫描 `.claude/agents/` 目录。

**`orchestrator.py`**：`SubAgentOrchestrator` 只有 90 行。`dispatch()` → `asyncio.gather([_run_one(t) for t in tasks])`。`_execute_task()`：创建子 Agent → `asyncio.wait_for(run_once(prompt), timeout)` → 合并 token 用量到父 Agent → 异常处理。超时后 `agent.abort()` 确保循环终止。

## 5. 设计决策

### 为什么安全靠工具白名单而非 sandbox

explore 和 plan 根本没有 `write_file` 和 `run_shell`——攻击面在工具注册层闭合。sandbox 只对有 `run_shell` 的子 Agent（general）有意义。最薄防线在最前面。

### 为什么递归防护靠"不给 agent 工具"

所有子 Agent 的 tool list 排除 `agent`——模型看不到这个工具。比深度计数器更简单可靠——不需要在运行时传递深度状态。单层嵌套已覆盖当前所有用例。

### 为什么子 Agent 共享父 Agent 的 SandboxManager

bwrap 隔离是 per-command 的——多建不增加隔离。microsandbox 多建又太重。复用父实例是务实选择。

## 6. 面试考点

**Q: 为什么 Explorer 和 Plan 工具相同？** 安全由白名单保证——3 个只读工具足够安全。差异在 prompt 驱动——Explorer 搜代码，Plan 产计划。

**Q: 并行子 Agent 文件冲突？** 已知局限。Codex CLI 用 git worktree 隔离——Nanocode 尚未实现。当前并行更多用于只读任务。

**Q: 递归防护怎么做的？** 工具列表排除 agent——模型看不到，无法创建子子 Agent。比深度计数器简单可靠。

**Q: plan Agent 和 Plan Mode 区别？** plan Agent 是工具级——带特殊提示词的只读 Agent。Plan Mode 是系统级——全局行为切换。后者在 roadmap 中。

## 7. 代码导读

**关键行号**：`__init__.py:20-65` EXPLORE/PLAN/GENERAL_PROMPT、`__init__.py:133-152` get_sub_agent_config()、`__init__.py:87-99` _discover_custom_agents()、`orchestrator.py:26-43` dispatch()、`orchestrator.py:45-92` _execute_task() 超时处理。
