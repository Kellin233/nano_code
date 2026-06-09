# 子 Agent 与计划模式

## 1. 为什么需要子 Agent

主 Agent 的上下文窗口有限。让它同时搜索代码、审查安全、跑测试——消息历史会被中间产物撑爆。子 Agent 解决这个问题：**把脏活累活 fork 到独立上下文，只把结果摘要带回来**。

这是 Codex CLI 多 Agent 模式的核心思想——"context isolation as architecture"。

## 2. 核心概念

### 2.1 Fork-and-Return

```
主 Agent 调用 agent 工具
  → get_sub_agent_config(type)  # 查内置或自定义配置
  → Agent(RuntimeConfig(is_sub_agent=True), custom_tools=..., sandbox_manager=父的)
  → agent.run_once(prompt)      # 独立消息历史，阻塞等结果
  → 返回文本 + token 用量
```

子 Agent 和主 Agent 是**同一个 Agent 类**的不同实例——通过 `custom_tools` 限制工具白名单，通过 `is_sub_agent` 跳过启动上下文和记忆系统。

### 2.2 三种内置类型

| 类型 | 工具 | 用途 |
|------|------|------|
| explore | read_file, list_files, grep_search | 搜索代码、探索项目 |
| plan | 同上 3 个只读工具 | 分析架构、输出步骤化计划 |
| general | 全工具 - agent | 独立完成任务 |

explore 和 plan 的工具白名单完全相同——差异纯粹靠 system prompt 驱动。explore 的 prompt 强调"快速并行搜索"，plan 的 prompt 强调"分析结构、列出步骤、识别风险"。

### 2.3 并行编排

`SubAgentOrchestrator.dispatch(tasks)` 接收任务列表，`asyncio.gather` 并行执行。`Semaphore` 控制最大并发（默认 4），`asyncio.wait_for` 控制单任务超时（默认 60s）。超时后 `agent.abort()` 确保循环终止。

### 2.4 计划模式

当前 `plan` 子 Agent 是工具级功能——模型调用 `agent(type="plan")` 生成计划。它和 Claude Code 的系统级 Plan Mode 不同——后者切换 Agent 全局行为（"先规划再执行"），当前只是一个带特殊提示词的只读 Agent。系统级 Plan Mode 在 roadmap 中（P1）。

## 3. 总体设计

```
capabilities/subagents/
├── __init__.py        # 3 种内置类型 + 自定义发现 + get_sub_agent_config()
└── orchestrator.py    # SubAgentOrchestrator 并行编排器（~90 行）
```

## 4. 详细设计

**`__init__.py`**：`EXPLORE_PROMPT`/`PLAN_PROMPT`/`GENERAL_PROMPT` 三个内置 system prompt。`READ_ONLY_TOOLS = {"read_file", "list_files", "grep_search"}`。`get_sub_agent_config(type)` 是核心函数——查自定义 Agent（`.claude/agents/*.md`）或返回内置类型配置。

**`orchestrator.py`**：`SubAgentOrchestrator` 只有 90 行。`dispatch(tasks)` → `asyncio.gather(*[_run_one(t) for t in tasks])`。`_execute_task()` 创建子 Agent 实例，`asyncio.wait_for(agent.run_once(prompt), timeout)`。超时后 `agent.abort()`。

## 5. 设计决策

### 为什么子 Agent 共享父 Agent 的 SandboxManager

bwrap 隔离是 per-command 的——多建 SandboxManager 不增加隔离。microsandbox 多建又太重。复用父实例是唯一合理做法。

### 为什么安全靠工具白名单

explore 和 plan 根本没有 `write_file` 和 `run_shell`——攻击面在工具注册层就闭合。sandbox 只对有 run_shell 的子 Agent（general）有意义。

### 为什么递归防护靠"不给 agent 工具"

所有子 Agent 的工具列表排除 `agent`——模型看不到这个工具，无法发起子子 Agent。比深度计数器更简单可靠。

## 6. 面试考点

**Q: 子 Agent 为什么不创建独立 sandbox？** bwrap 隔离是 per-command 的，多建不增加隔离。

**Q: 并行子 Agent 文件冲突怎么办？** 已知局限。Codex CLI 用 git worktree 隔离。Nanocode 尚未实现。

**Q: plan Agent 和 Plan Mode 有什么区别？** Plan Agent 是工具级——带特殊提示词的只读 Agent。Plan Mode 是系统级——全局行为切换。后者在 roadmap 中。

## 7. 代码导读

**关键代码**：`__init__.py:133-152` get_sub_agent_config()、`orchestrator.py:26-43` dispatch()、`orchestrator.py:45-92` _execute_task() 超时处理。
