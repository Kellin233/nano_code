# 代码导读

## 1. 推荐阅读顺序

跟一条请求从入口走到工具执行：

```
1. cli/main.py
2. cli/session.py
3. agent/agent.py
4. agent/loop.py
5. providers/anthropic.py
6. cli/core/tools/runtime.py
7. agent/runtime_management/compressor.py
8. agent/runtime_management/context/builder.py
```

只读 3 个文件理解主路径：

```
cli/session.py
agent/loop.py
cli/core/tools/runtime.py
```

## 2. 关键文件

| 文件 | 为什么重要 |
|------|------------|
| `cli/session.py` | 唯一装配点，连接 Agent、Backend、ToolRuntime、Memory、MCP、Hooks、Extensions |
| `agent/agent.py` | Agent 状态容器、消息历史、预算、回调槽位 |
| `agent/loop.py` | LLM/tool 循环，只依赖注入回调 |
| `agent/types.py` | core 协议类型：ConversationHistory、ToolCall、ToolResult、RuntimeEvent |
| `providers/base.py` | Backend 抽象和统一返回结构 |
| `providers/anthropic.py` | Anthropic 流式解析和 tool_use 转换 |
| `providers/openai.py` | OpenAI-compatible 流式解析和 function call 转换 |
| `cli/core/tools/runtime.py` | 工具执行管线 |
| `cli/core/tools/registry.py` | ToolRegistry、deferred、MCP/extension 工具注册 |
| `agent/runtime_management/compressor.py` | 三层上下文治理中的 Tool History Snip 和 Context Compact |
| `agent/runtime_management/context/sources.py` | AGENTS.md、.nanocode/rules、Git 快照、frontmatter |
| `agent/runtime_management/persistence/session_log.py` | session.jsonl checkpoint/resume |
| `agent/runtime_management/persistence/run_store.py` | run trace/report 持久化 |
| `cli/core/extensions/runner.py` | Extension 事件分发和错误隔离 |

## 3. 常见修改路径

| 需求 | 改哪里 |
|------|--------|
| 加内置工具 | `cli/core/tools/builtin.py` + 必要时更新 registry/types 测试 |
| 加扩展工具入口 | `cli/core/extensions/api.py` / `runner.py` |
| 加模型厂商 | `providers/<name>.py` + `providers/__init__.py` + `agent/models.py` |
| 改主循环 | `agent/loop.py`，尽量通过注入回调保持 core 纯净 |
| 改装配关系 | `cli/session.py` |
| 改权限规则 | `agent/runtime_management/permissions/` |
| 改 sandbox profile | `cli/core/sandbox/config.py` / `types.py` |
| 改上下文模板 | `agent/runtime_management/context/builder.py` |
| 改压缩策略 | `agent/runtime_management/compressor.py` |
| 改 checkpoint/resume | `agent/runtime_management/persistence/session_log.py` + `cli/session.py` |
| 改 run artifacts / benchmark report | `agent/runtime_management/persistence/run_store.py`、`report.py`、`benchmarks/local-fixture/run.py` |
| 改 TUI 命令 | `tui/commands.py` / `tui/app.py` |
| 改 server protocol | `cli/core/protocol/messages.py` / `cli/core/server/app_server.py` |

## 4. 设计模式速查

| 模式 | 位置 |
|------|------|
| 策略模式 | `providers/`: AnthropicBackend / OpenAIBackend |
| 工厂函数 | `providers/create_backend()`、`agent/events.py` |
| 注册表模式 | `cli/core/tools/registry.py` |
| 依赖注入 | `AgentLoop(..., execute_tools=...)` |
| 回调桥接 | `Agent.set_callbacks()` + `ExtensionRunner` |
| 事件流 | `AgentLoop.run()` / `RuntimeThread.submit()` |
| 外观/装配点 | `cli/session.py: AgentSession` |

## 5. 不要破坏的边界

- 不要让 `agent/` import `cli/`、`tui/`、`providers/`、SDK 或 `cli/core/`。
- 不要让 `agent/runtime_management/` import `cli/`、`tui/`、`providers/`。
- 不要在 `providers/` 中接触 AgentSession 或 TUI。
- 不要在 `agent/loop.py` 里直接创建 ToolRuntime。
- 新能力优先放到 `cli/core/`，由 `cli/session.py` 装配。
