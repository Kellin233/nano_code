# 引言

## NanoCode 是什么

NanoCode 是一个轻量级编程智能体 CLI 工具，受 Claude Code 启发，纯 Python 实现。支持 Anthropic 和 OpenAI 兼容接口，内置 TUI 交互模式和 headless server 模式。

**一句话定位**：把 LLM 变成能读代码、写文件、跑命令的编程助手，跑在你的终端里。

## 核心设计哲学

### 1. Agent 是纯状态容器

Agent 类只持有状态字段（消息历史、token 计数、配置），不实现任何行为。对话循环、API 调用、上下文压缩分别由 `AgentLoop`、`Backend`、`Compressor` 实现。这是从 Mixin 模式改过来的——消除了隐式耦合，每个模块有独立的变更原因。

### 2. Backend 是策略类

Anthropic 和 OpenAI 的流式调用差异被封装在 `AnthropicBackend` 和 `OpenAIBackend` 两个策略类中。`AgentLoop` 只依赖 `Backend` 接口，不关心具体厂商。新增模型厂商只需加一个文件。

### 3. 独立变更原因决定文件拆分

不按"有没有关系"拆分，按"会不会一起改"拆分。改 A 时总得同时改 B，就放在一个文件；能独立演化，就可以分开。这避免了过度拆分，也避免了 God-file。

### 4. 能力模块保持共同模板

每个 `capabilities/<name>/` 子目录遵循相同的结构约定——`types.py` 定义数据模型，引擎文件按变更原因拆 N 个。一致性降低学习成本，看一个就能推断其他。

## 技术栈

- Python >= 3.10，`asyncio` + `dataclasses` + `pathlib`
- 核心依赖：`anthropic`、`openai`、`prompt_toolkit`、`rich`
- 可选依赖：`microsandbox`（容器沙箱）
- 构建：`setuptools`，入口 `nanocode.cli.main:main`

## 架构全景图

```
                    cli / tui / server        ← 表现层：用户如何交互
                           │
                    runtime/  ★内核★          ← Agent 状态 + 主循环 + 压缩 + 事件
                    ╱         ╲
              backend/     capabilities/      ← 模型后端（策略类） / 能力模块
                           │
                context/    models.py         ← 上下文构建 / 模型元数据
                session/    protocol/         ← 持久化 / 协议层
```

**依赖方向单向**：表现层 → runtime → backend / capabilities / context / models。下层不反向引用上层。

## 一次用户请求的完整路径

```
用户输入 "修复 agent.py 的 bug"
  → cli/main.py 创建 Agent + Backend + AgentLoop
  → AgentLoop.run(prompt)
      → 注入启动上下文（CLAUDE.md、Git 快照、日期/平台）
      → 记忆召回（文件匹配 + LLM 精选）
      → Backend.call() 调用模型
      → 模型返回 tool_calls
      → ToolRuntime 执行工具（验证→权限→确认→执行）
      → 结果追加到消息历史
      → 压缩检查（Budget/Snip/Microcompact）
      → 循环直到模型不再调用工具
  → 渲染 RuntimeEvent 到终端
  → 保存会话
```

## 关键数字

| 指标 | 数值 |
|------|:--:|
| 源码文件数 | ~55 |
| 测试文件数 | ~35 |
| 测试总数 | 275 |
| 测试通过率 | 98.1% |
| 模块数 | 11 顶层模块 |
| capabilities 子模块 | 7 个 |

## 源码模块速览

| 模块 | 职责 |
|------|------|
| `cli/` | CLI 入口，参数解析 + 依赖组装 |
| `tui/` | 终端 UI，交互式 REPL |
| `server/` | JSONL 协议 server |
| `runtime/` | **内核**：Agent 状态、主循环、压缩、事件 |
| `backend/` | 模型后端策略类（Anthropic/OpenAI） |
| `capabilities/` | 7 个能力模块（tools/mcp/skills/hooks/memory/sandbox/permissions/subagents） |
| `context/` | System prompt + 启动上下文 + 动态附件 |
| `models.py` | 模型元数据（上下文窗口、thinking、重试） |
| `protocol/` | JSONL 消息协议 |
| `session/` | 会话持久化 |
