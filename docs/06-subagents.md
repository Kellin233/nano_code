# 子 Agent 与计划模式

## 为什么需要子 Agent

主 Agent 的上下文窗口是有限的。让它同时搜索代码、审查安全、跑测试——消息历史会被中间产物撑爆。子 Agent 解决这个问题：**把脏活累活 fork 到独立上下文里，只把结果摘要带回来**。

这是 Codex CLI 的多 Agent 模式的核心思想——"context isolation as architecture"。NanoCode 做了同样的设计选择。

## 核心概念

### Fork-and-Return

```
主 Agent 调用 agent 工具
  → get_sub_agent_config(type)  # 查内置 explore/plan/general 或自定义 .md
  → Agent(RuntimeConfig(is_sub_agent=True), custom_tools=..., sandbox_manager=父的)
  → agent.run_once(prompt)      # 独立消息历史，阻塞等结果
  → 返回文本结果 + token 用量
```

子 Agent 和主 Agent 是**同一个 Agent 类**的不同实例——通过 `custom_tools` 限制工具白名单，通过 `is_sub_agent=True` 跳过启动上下文和记忆系统。

### 三种内置类型

| 类型 | 工具 | 用途 |
|------|------|------|
| explore | read_file, list_files, grep_search | 搜索代码、探索项目 |
| plan | 同上 3 个只读工具 | 分析架构、输出步骤化计划 |
| general | 全工具 - agent | 独立完成任务 |

explore 和 plan 的工具白名单完全相同——差异纯粹靠 system prompt 驱动。explore 的 prompt 强调"快速并行搜索"，plan 的 prompt 强调"分析结构、列出步骤、识别风险"。

### 并行编排

`SubAgentOrchestrator.dispatch(tasks)` 接收任务列表，`asyncio.gather` 并行执行。`Semaphore` 控制最大并发数（默认 4），`asyncio.wait_for` 控制单任务超时（默认 60s）。子 Agent 复用父 Agent 的 SandboxManager。

### 计划模式（plan subagent 和 Plan Mode 的区别）

当前 `plan` 子 Agent 是一个**工具级功能**——模型调用 `agent(type="plan")` 来生成计划。它和 Claude Code 的系统级 Plan Mode 不一样——后者切换 Agent 的全局行为（"先规划再执行"），前者只是一个带特殊提示词的只读 Agent。

系统级 Plan Mode 被列在 roadmap 里（P1），不属于当前实现。

## 设计决策

### 为什么子 Agent 共享父 Agent 的 SandboxManager

bwrap 的隔离是 per-command 的——每次 `run_shell()` 都是新 bwrap 进程。创建多个 SandboxManager 实例不会带来额外隔离——只会增加不必要的配置副本。microsandbox 创建多个 microVM 又太重。复用父实例是唯一合理做法。

### 为什么子 Agent 安全靠工具白名单而非 sandbox

explore 和 plan 根本没有 `write_file` 和 `run_shell`——攻击面在工具注册层就闭合了。sandbox 只对有 `run_shell` 的子 Agent（general）有意义。这是"最薄防线在最前面"的安全原则。

### 为什么递归防护靠"不给 agent 工具"而非深度计数器

所有子 Agent 的 tool list 排除 `agent`——模型根本看不到这个工具，无法发起子子 Agent。这比"深度计数器"更简单可靠——不需要在运行时传递深度状态。

## 代码走读

**`__init__.py`**：3 种内置类型的 system prompt 定义 + 自定义 Agent 发现（`~/.claude/agents/*.md` 和 `.claude/agents/*.md`）。`get_sub_agent_config(type)` 返回 `{"system_prompt": str, "tools": list}`。

**`orchestrator.py`**：`SubAgentOrchestrator` 只有 90 行。`dispatch()` → `_execute_task()` → `asyncio.wait_for(agent.run_once(prompt), timeout)`。超时后 `agent.abort()` 确保循环终止。

## 面试考点

**Q: 为什么子 Agent 不创建独立 sandbox？**

bwrap 隔离是 per-command 的——多建 SandboxManager 不增加隔离。microsandbox 多建又太重。复用父实例是务实选择。

**Q: 并行子 Agent 互相干扰怎么办？**

文件修改会冲突——这是已知局限。Codex CLI 用 git worktree 隔离。NanoCode 尚未实现（roadmap 考虑中）。当前并行更多用于只读任务（多个 explore Agent 同时搜索）。
