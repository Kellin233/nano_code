# 引言

## 1. 为什么需要 NanoCode

LLM 只能生成文本。让它读文件、搜索代码、跑命令、写文件，需要有人把模型输出翻译成工具调用，把工具结果喂回模型，并持续循环直到任务完成。

NanoCode 的定位是终端里的轻量编程 Agent。它支持 Anthropic 和 OpenAI-compatible 后端，提供工具调用循环、权限确认、shell sandbox、上下文压缩、TUI、headless server、MCP、skills、memory、hooks 和扩展系统。

当前实现的设计偏好是：**轻量、可学习、可审计、分层明确**。核心不是把所有能力塞进 Agent，而是让 Agent core 保持纯净，由应用层按需装配能力。

## 2. 核心原则

| 原则 | 含义 |
|------|------|
| Agent core 只管状态机和协议 | `agent/agent.py`、`agent/loop.py`、`agent/events.py`、`agent/types.py` 不认识具体工具、插件、TUI、文件系统或 SDK |
| Harness 只管怎么运转 | `agent/harness/` 放压缩、上下文构建、会话持久化、权限、approvals、hooks，可以 I/O，但不依赖 `cli/`、`tui/`、`providers/` |
| 能力模块在应用层 | `cli/core/` 放 tools、sandbox、skills、memory、mcp、subagents、server/protocol、extensions |
| AgentSession 是唯一装配点 | `cli/session.py` 创建 Agent、Backend、ToolRuntime、MemoryRuntime、MCP、HookManager、ExtensionRunner，并桥接回调 |
| Provider 层独立 | `providers/` 只依赖 `agent/types.py`，新增厂商不改 Agent core |

## 3. 架构全景

```
表现层
  cli/        tui/        cli/core/server/
      \        |              /
       \       |             /
        └── cli/session.py  ← AgentSession，唯一装配点
              │
              ├── cli/core/         ← 应用能力层
              │   tools / sandbox / skills / memory / mcp / subagents / extensions / protocol
              │
              ├── providers/        ← 模型厂商策略
              │
              ├── agent/harness/    ← 运行框架
              │   compressor / context / session / permissions / hooks / approvals
              │
              └── agent/            ← 纯内核
                  Agent / AgentLoop / RuntimeEvent / core types
```

依赖方向只向下：

```
cli/tui/server → cli/session.py → cli/core/
cli/session.py → providers/ → agent/types.py
cli/session.py → agent/harness/ → agent/
cli/session.py → agent/
```

反向依赖是不允许的。Agent core 不 import `cli/core`，不 import `agent/harness`，不 import `providers`，也不 import OpenAI/Anthropic SDK。

## 4. 一条请求如何运行

```
用户输入 "修这个 bug"
    │
    ├── cli/main.py
    │     └── create_session(config)
    │
    ├── cli/session.py: AgentSession
    │     ├── Agent(config)
    │     ├── create_backend(config)
    │     ├── ToolRegistry + ToolRuntime
    │     ├── SandboxManager / McpManager / SkillInvocation / MemoryRuntime
    │     ├── HookManager.capture()
    │     ├── ExtensionRunner + load_extensions()
    │     └── AgentLoop(agent, backend, execute_tools=...)
    │
    ├── agent/loop.py: AgentLoop.run()
    │     ├── 注入 startup context 和动态附件
    │     ├── 调 provider backend
    │     ├── 收到 tool_calls
    │     ├── 调注入的 execute_tools 回调
    │     ├── 追加 tool results
    │     └── 继续循环，直到模型停止或预算耗尽
    │
    ├── cli/core/tools/runtime.py
    │     └── 验证 → extension before hook → hooks → 权限 → 确认 → 执行 → 持久化大结果 → extension after hook
    │
    └── RuntimeEvent 流
          ├── CLI 一次性渲染
          ├── TUI Rich 渲染
          └── Server JSONL 转发
```

## 5. 主要模块

| 模块 | 职责 | 关键边界 |
|------|------|---------|
| `agent/` | Agent 状态、LLM/tool 循环、事件、核心类型、模型元数据和费用估算 | 不持有具体能力，不 import 应用层 |
| `agent/harness/` | 压缩、上下文构建、会话持久化、权限、approvals、hooks | 可以 I/O，不依赖表现层和 provider |
| `providers/` | Anthropic/OpenAI-compatible 调用、流式解析、统一返回 `BackendResponse` | 只依赖 core types |
| `cli/session.py` | 创建并连接所有运行对象 | 唯一装配点 |
| `cli/core/tools/` | 工具 schema、注册、执行管线、deferred 激活 | ToolRuntime 由 Session 创建 |
| `cli/core/sandbox/` | `run_shell` 的执行隔离 | 只管执行边界，不替代权限 |
| `cli/core/skills/` | Skill 发现、参数渲染、active skill 管理 | Skill 是提示词模板，不是代码插件 |
| `cli/core/memory/` | 文件式长期记忆、召回、LLM side-query 精选 | MemoryRuntime 由 Session 调用 |
| `cli/core/mcp/` | MCP server 连接、工具聚合、资源读取 | MCP 工具进入 ToolRegistry |
| `cli/core/extensions/` | 进程内 Python 扩展，注册工具、命令、事件订阅 | Agent core 不感知扩展存在 |
| `tui/` | 交互式 REPL 和 Rich 渲染 | 只消费 Session/Thread 暴露的接口 |

## 6. Hook 和 Extension 的关系

Hook 和 Extension 不是替代关系。

| 维度 | Hook | Extension |
|------|------|-----------|
| 位置 | `agent/harness/hooks/` | `cli/core/extensions/` |
| 形式 | 外部进程，JSON stdin/stdout | 进程内 Python `.py` |
| 适合 | deny/allow/modify/append_context 这类简单拦截 | 注册工具、注册命令、订阅事件 |
| 装配 | AgentSession 创建 HookManager | AgentSession 加载 ExtensionRunner |
| 触发 | AgentLoop 和 ToolRuntime | Agent 回调槽位和 ToolRuntime before/after |

## 7. 推荐阅读顺序

```
1. cli/session.py                 # 看唯一装配点
2. agent/agent.py                 # 看 Agent 保存哪些状态和回调槽位
3. agent/loop.py                  # 看主循环如何只依赖注入回调
4. providers/anthropic.py         # 看模型响应如何统一成 BackendResponse
5. cli/core/tools/runtime.py      # 看工具执行管线
6. agent/harness/compressor.py    # 看上下文压缩
7. agent/harness/context/builder.py
```
