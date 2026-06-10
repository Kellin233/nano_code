# 子 Agent 与计划模式

## 1. 为什么需要子 Agent

主 Agent 的上下文窗口有限。搜索、审查、跑测试产生的大量中间结果会污染主会话。子 Agent 的作用是把探索性任务放到独立上下文中执行，只把摘要结果带回主会话。

子 Agent 是应用层能力，位于 `cli/core/subagents/`。创建和运行由 `AgentSession` 负责，Agent core 不直接认识子 Agent 编排器。

## 2. 文件结构

```
cli/core/subagents/
├── __init__.py        # 内置类型、自定义 agent 发现、get_sub_agent_config()
└── orchestrator.py    # SubAgentOrchestrator 并行编排
```

## 3. Fork-and-Return

```
主 Agent 调用 agent 工具
    │
    ├── get_sub_agent_config(type)
    │     ├── 查 .claude/agents/*.md
    │     └── fallback 内置 explore / plan / general
    │
    ├── AgentSession 创建子会话
    │     ├── RuntimeConfig(is_sub_agent=True)
    │     ├── custom_system_prompt
    │     ├── custom_tools 白名单
    │     └── 复用父 SandboxManager
    │
    └── child_session.run_once(prompt)
          → 独立消息历史
          → 独立 AgentLoop
          → 返回 text + token usage
```

子 Agent 和主 Agent 共享代码，不共享消息历史。它们通过 `RuntimeConfig.is_sub_agent` 控制行为差异：跳过 startup context、不初始化 MCP、不触发记忆召回。

## 4. 内置类型

| 类型 | 工具白名单 | 用途 |
|------|-----------|------|
| `explore` | `read_file`、`list_files`、`grep_search` | 搜索代码、定位相关文件 |
| `plan` | 同上 | 分析方案、拆解任务、识别风险 |
| `general` | 全工具但排除 `agent` | 独立完成较完整任务 |

递归防护靠工具列表排除 `agent`。模型看不到 agent 工具，就无法创建子子 Agent。

## 5. 自定义 Agent

`.claude/agents/*.md`：

```yaml
---
name: code-reviewer
description: 审查代码变更
allowed-tools: read_file, grep_search, list_files, run_shell
---
... system prompt body ...
```

项目级覆盖用户级。`allowed-tools` 是白名单；未声明时默认给全工具但排除 `agent`。

## 6. plan 子 Agent vs 系统级 Plan Mode

`agent(type="plan")` 是工具级功能：它创建一个只读子 Agent 产出计划文本。主 Agent 是否遵循计划，仍由模型自己决定。

系统级 Plan Mode 是全局行为切换：先规划、用户确认后再允许修改。当前尚未实现，见 roadmap。

## 7. 设计决策

### 为什么子 Agent 走 AgentSession

子 Agent 也需要 Backend、ToolRuntime、Sandbox、hooks、skills 等装配。复用 `AgentSession` 可以避免为子 Agent 重写一套 glue code。

### 为什么共享 SandboxManager

bwrap 是 per-command 隔离，多建一个 manager 不增加安全性。microsandbox 多建成本高。复用父会话的 sandbox 是更务实的选择。

### 为什么 explore 和 plan 工具相同

安全边界由只读工具白名单保证。二者差异主要来自 system prompt：explore 强调搜索事实，plan 强调分析方案。

## 8. 代码导读

```
cli/core/subagents/__init__.py
cli/core/subagents/orchestrator.py
cli/session.py::_execute_agent_tool
cli/session.py::run_once
```
