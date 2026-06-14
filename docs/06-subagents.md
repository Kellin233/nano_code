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
│     ├── allowed_tools = 父运行白名单 ∩ task/skill 白名单
│     └── 复用父 SandboxManager
│
└── child_session.run_once(prompt)
          → 独立消息历史
          → 独立 AgentLoop
          → 返回 text + token usage
```

子 Agent 和主 Agent 共享代码，不共享消息历史。它们通过 `RuntimeConfig.is_sub_agent` 控制行为差异：跳过 startup context、不初始化 MCP、不触发记忆召回。

`SubAgentOrchestrator` 默认最多并发 4 个 task。单 task 默认超时 60 秒，默认最多 20 个 agentic turns；task 可以覆盖 `timeout` 和 `max_turns`。子 Agent 的 token usage 会累加回父 Agent 的总 usage。

Fork-and-return 的关键不是“多一个模型调用”，而是隔离探索过程：

- 子 Agent 有自己的 `ConversationHistory`，搜索、读取、试错不会污染主会话。
- 子 Agent 复用父会话 provider/sandbox 配置，避免产生另一套运行环境。
- 子 Agent 的结果被格式化为摘要文本返回主 Agent，主 Agent 再决定是否采用。
- 子 Agent 的 token 会回填父 Agent usage，费用和预算统计仍然完整。

这适合“信息量大但最终只需要结论”的任务，例如跨目录搜索、方案比较、批量审查。它不适合需要主 Agent 持续持有细粒度中间状态的任务。

## 4. 内置类型

| 类型 | 工具白名单 | 用途 |
|------|-----------|------|
| `explore` | `read_file`、`list_files`、`grep_search` | 搜索代码、定位相关文件 |
| `plan` | 同上 | 分析方案、拆解任务、识别风险 |
| `general` | 全工具但排除 `agent` | 独立完成较完整任务 |

递归防护靠工具列表排除 `agent`。模型看不到 agent 工具，就无法创建子子 Agent。

三类内置 Agent 的边界：

- `explore`：强调快速搜索和事实定位，只能读文件、列文件、grep。适合“找相关实现在哪里”。
- `plan`：同样只读，但 system prompt 强调方案、风险和修改点。适合“改之前先分析”。
- `general`：可以使用普通工具，但仍排除 `agent`。适合把相对独立的小任务交出去做完。

如果任务需要写文件，不能用 `explore` 或 `plan`。如果任务可能无限递归派生，必须确认 `agent` 不在子会话工具列表里。

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

自定义 Agent 发现后会动态 patch `agent` 工具 schema：顶层 `type` 和 `tasks[].type` 的 enum 都会包含 `.claude/agents/*.md` 中的自定义名称。这样模型可以合法地产生自定义 agent 类型，而工具层仍保持静态内置 schema。

当前发现路径是用户级 `~/.claude/agents` 加进程当前目录下 `.claude/agents`；后加载的项目级同名 agent 覆盖用户级。

父会话的 `RuntimeConfig.allowed_tools` 会传入子会话。单个 task 或 fork skill 也可以传 `allowed_tools`，子会话最终使用二者交集；这只会收窄工具，不会扩大父会话限制。

自定义 Agent 的 frontmatter 只决定子会话 system prompt 和工具白名单，不会改变父会话权限模式、sandbox profile、provider 或 workspace。项目级自定义 Agent 适合沉淀团队内常用角色，例如只读审查、迁移规划、测试定位；如果需要注册新工具，应使用 Extension 或 MCP，而不是 custom agent。

## 6. plan 子 Agent vs 系统级 Plan Mode

`agent(type="plan")` 是工具级功能：它创建一个只读子 Agent 产出计划文本。主 Agent 是否遵循计划，仍由模型自己决定。

系统级 Plan Mode 是全局行为切换：先规划、用户确认后再允许修改。当前代码没有实现这个全局模式。

面试式区分：

- `agent(type="plan")` 是一次工具调用，返回计划文本，主 Agent 仍可继续执行。
- 系统级 Plan Mode 应该是整个 runtime 的权限/交互策略，当前不存在。
- `plan` 子 Agent 的只读性来自工具白名单，不来自权限模式。
- 子 Agent 的结果不是自动执行计划，只是给主 Agent 的上下文。

## 7. 设计决策

### 为什么子 Agent 走 AgentSession

子 Agent 也需要 Backend、ToolRuntime、Sandbox、hooks、skills 等装配。复用 `AgentSession` 可以避免为子 Agent 重写一套 glue code。

### 为什么共享 SandboxManager

bwrap 是 per-command 隔离，多建一个 manager 不增加安全性。microsandbox 多建成本高。复用父会话的 sandbox 是更务实的选择。

### 为什么 explore 和 plan 工具相同

安全边界由只读工具白名单保证。二者差异主要来自 system prompt：explore 强调搜索事实，plan 强调分析方案。

## 8. Benchmark 覆盖

当前 `benchmarks/local-fixture` 没有专门的 sub-agent case。子 Agent 仍受核心合同覆盖：它复用同一套 `AgentSession`、`ToolRuntime`、权限、sandbox 和 allowed tools 收敛逻辑。新增 sub-agent benchmark 时应重点覆盖并发 task 顺序、父/子 allowed tools 交集、以及 fork skill 的工具边界。

维护者排查子 Agent 问题时先看三件事：

- `agent` 工具 schema 是否包含自定义 agent type。
- 子会话 `custom_tools` 和 `allowed_tools` 交集后是否为空或过窄。
- 父会话是否把子 Agent token usage 合并回总 usage。

## 9. 代码导读

```
cli/core/subagents/__init__.py
cli/core/subagents/orchestrator.py
cli/session.py::_execute_agent_tool
cli/session.py::run_once
```
