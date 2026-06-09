# 总体设计与架构

## 架构全景图

```
                         cli / tui / server        ← 表现层：用户如何交互
                                │
                         runtime/  ★内核★          ← Agent 状态 + 主循环 + 压缩 + 事件
                         ╱         ╲
                   backend/     capabilities/      ← 模型后端（策略类） / 能力模块（共同模板）
                                │
                     context/    models.py         ← 上下文构建 / 模型元数据
                     session/    protocol/         ← 持久化 / 协议层
```

**依赖方向单向**：表现层 → runtime → backend / capabilities / context / models。下层不反向引用上层。

## 模块全景图

| 模块 | 职责 | 说明 |
|------|------|------|
| `cli/` | CLI 入口 | `main.py` 组装依赖，`args.py` 解析参数+配置 |
| `tui/` | 终端 UI | TUI 生命周期、输入处理、渲染、REPL 命令、主题 |
| `server/` | Server 模式 | JSONL 协议 server + stdio/websocket/unix transport |
| `runtime/` | **Agent Runtime 内核** | `agent.py` 状态容器、`loop.py` 主循环、`compressor.py` 压缩、`events.py` 事件 |
| `backend/` | 模型后端 | `base.py` 接口 + `anthropic.py`/`openai.py` 策略实现 |
| `capabilities/` | 能力模块 | tools/mcp/skills/hooks/memory/sandbox/permissions/subagents |
| `context/` | 上下文构建 | `builder.py` 组装 system prompt、`sources.py` 数据源 |
| `models.py` | 模型元数据 | 上下文窗口、thinking 支持、输出 token、重试策略 |

### capabilities/ 子模块

```
capabilities/
├── tools/         # 5 文件：types, builtin, registry, runtime
├── mcp/           # 8 文件：types, config, manager, connection, transport, output, resources
├── skills/        # 4 文件：types, registry, runtime, prompt
├── hooks/         # 3 文件：types, config, runner
├── memory/        # 4 文件：types, store, retrieval, consolidation
├── sandbox/       # 6 文件：types, config, manager, backend, bwrap, microsandbox
├── permissions/   # 4 文件：policy, rules, workspace, shell
└── subagents/     # 2 文件：__init__ (类型+发现), orchestrator (并行编排)
```

每个子模块遵循共同模板：`types.py`（数据模型）+ 引擎文件（按独立变更原因拆 N 个）。

## 关键设计决策

### Agent 是纯状态容器

`runtime/agent.py` 只持有字段和简单 getter/setter，不包含对话循环、API 调用、压缩策略。行为分给 `loop.py`（循环）、`backend/`（API 调用）、`compressor.py`（压缩）。子 Agent fork 复用同一个类。

**为什么**：原架构用 Mixin 拼装 Agent 行为——Mixin 通过 `self._xxx` 隐式访问状态，跨 4 个文件阅读，耦合不可见。

### Backend 是策略类

`backend/base.py` 定义 `Backend` 抽象接口，`anthropic.py` 和 `openai.py` 各自实现。`loop.py` 只依赖接口。

**为什么**：原架构 Backend 是 Agent 的 Mixin，新增模型厂商需要改 Agent 核心。现在只需加一个文件。

### 双消息历史刻意不统一

`_anthropic_messages` 和 `_openai_messages` 分开存储。Anthropic 的 `tool_use/tool_result` block 和 OpenAI 的 `function call/role:tool` 格式差异太大，强行统一增加复杂度。`Agent` 的公开方法在内部根据 `use_openai` 路由。

### 事件用工厂函数而非子类

```python
ToolCallStarted(call)    # → RuntimeEvent(type="tool.started")
LoopFinished("stop")     # → RuntimeEvent(type="turn.finished")
```

不需要 `isinstance` 判断链。类型判断用 `event.type` 字符串。

### capabilities 不抽象统一基类

每个 capability 的接口天然不同——tools 需要 registry + runtime，hooks 只需要 runner + config。抽象出 `Capability` 基类只会增加约束而不增加价值。

## 代码划分原则

1. **独立变更原因**——改 A 时是否几乎总得同时改 B？是则合并，否则可以分开
2. **能力模块保持共同模板**——每个 `capabilities/<name>/` 的结构约定一致
3. **依赖方向单向**——`backend/` 不引用 `runtime/`，`capabilities/` 不引用 `runtime/`

## 一次完整请求的数据流

```
用户: "修复 agent.py 的 bug"
  → cli/main.py: 创建 Agent + Backend + AgentLoop
  → AgentLoop.run(prompt)
      → 注入启动上下文（CLAUDE.md, Git, 日期/平台）
      → 记忆召回
      → Backend.call() → 模型返回 tool_calls
      → ToolRuntime 执行工具 → 结果追加历史
      → 压缩检查 → 循环直到模型不再调用工具
  → RuntimeEvent 流渲染到 TUI/CLI
  → 会话自动保存
```

## 文件数量

| 模块 | 文件数 |
|------|:--:|
| `cli/` | 2 |
| `runtime/` | 4 |
| `backend/` | 3 |
| `capabilities/` | 32 |
| `context/` | 2 |
| 其他（tui/server/protocol/session/models/logging） | 15 |
| **总计** | **58** |
