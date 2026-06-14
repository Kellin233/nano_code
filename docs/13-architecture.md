# 架构对比

## 1. NanoCode 当前架构

NanoCode 的核心架构是四层单向依赖：

```
表现层：cli / tui / cli/core/server
应用层：cli/session.py + cli/core/*
框架层：agent/harness/*
内核层：agent/*
Provider：providers/* 只依赖 agent/types.py、agent/models.py 的纯 helper 和 provider SDK
```

关键点：

- Agent core 不认识工具、插件、TUI、SDK。
- AgentSession 是唯一装配点。
- 能力模块在 `cli/core/` 组合和裁剪。
- Extension 是进程内 Python 扩展，Hook 是外部进程拦截面。
- Provider 层独立，新增厂商不改 AgentLoop。

## 2. 横向对比

| 维度 | NanoCode | Claude Code | Codex CLI | Aider |
|------|:--:|:--:|:--:|:--:|
| Agent 内核 | 纯状态 + loop + 回调槽位 | 内部 Agent SDK | Agent loop + SDK | 单 Agent |
| 装配点 | `AgentSession` | 产品内部 harness | CLI runtime | Coder 对象 |
| 能力模块 | `cli/core/*` 可裁剪 | 内建能力 | 内建 + config | Coder 方法 |
| 扩展面 | Hook + Extension | Hooks/commands/插件式能力 | 配置和工具扩展 | 较少 |
| 子 Agent | Fork-and-Return | Worktree + Skill | Multi-Agent + CSV | 无 |
| Sandbox | bwrap/local/microsandbox | 容器级 | OS 级 | 无内置 |
| 上下文压缩 | Tool Result Budget / Tool History Snip / Context Compact | 自动 | 自动 | Map/Reduce |
| 记忆 | 轻量 Markdown topic + 启动注入 | 文件式 | 内建持久化 | 无 |
| MCP | stdio transport | 完整 MCP | 内建 | 社区 |

## 3. 与 Claude Code 的差异

NanoCode 更强调源码可读和显式分层。Agent core 只保留状态机和协议，能力通过 `AgentSession` 装配；Claude Code 的内部实现更产品化、更重。

NanoCode 同时支持 Anthropic 和 OpenAI-compatible provider，因此不能把 Anthropic 专属能力写进 core。上下文压缩也通过注入 summary callable 避免 harness 依赖 provider。

## 4. 与 Codex CLI 的差异

NanoCode 借鉴了 Codex CLI 的一些分层思想，例如审批/sandbox/agent loop 的解耦，但实现更小。Codex CLI 的多 Agent 和 worktree 隔离更成熟；NanoCode 当前子 Agent 共享 workspace，适合只读探索和轻量并行。

## 5. NanoCode 的独特设计点

1. Agent core 纯净：状态机和协议不依赖应用能力。
2. `AgentSession` 单装配点：所有 tools/memory/MCP/extensions 都在这里桥接。
3. Hook 和 Extension 双扩展面：外部进程拦截与进程内 Python 扩展分工明确。
4. Provider 独立：厂商 SDK 只出现在 `providers/`。
5. durable session log + run trace/report 分离：resume 与诊断互不抢 source of truth。
6. 三层上下文治理：单结果预算落盘、历史工具结果裁剪、保留最近原文的 Context Compact。

## 6. 已知代价

- `AgentSession` 是集中装配点，文件会比普通模块更“胶水化”。
- 子 Agent 尚未用 git worktree 隔离，并行写文件有冲突风险。
- Extension 是进程内 Python，能力强但需要信任扩展代码。
- 测试目录名仍沿用旧模块名，虽然 import 已迁移到新包路径。
