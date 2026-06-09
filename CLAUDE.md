# NanoCode CLAUDE.md

用中文交流。代码、文件名、命令保留英文。

## 项目定位

NanoCode 是一个轻量级编程智能体 CLI 工具，受 Claude Code 启发，纯 Python 实现。
支持 Anthropic 和 OpenAI 兼容接口，内置 TUI 交互模式和 headless server 模式。

## 技术栈

- Python >= 3.10，`asyncio` + `dataclasses` + `pathlib`
- 核心依赖：`anthropic`、`openai`、`prompt_toolkit`、`rich`
- 可选依赖：`microsandbox`（容器沙箱）
- 构建：`setuptools`，入口 `nanocode.__main__:main`
- Lint：`ruff`，类型检查：`mypy`（宽松模式）

## 常用命令

```bash
python -m pip install -e .
python -m nanocode "hello"                    # 一次性执行
python -m nanocode                            # 交互式 REPL
python -m nanocode --server stdio             # Server 模式
python -m compileall src test                 # 编译检查
python -m unittest discover -s test -v        # 全部测试
python -m unittest discover -s test/v1 -v     # 重构/回归测试
```

只有任务明确需要时才进行真实 API 调用。

## 架构概览

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

### 各模块一句话

| 模块 | 职责 | 说 明 |
|------|------|------|
| `cli/` | CLI 入口 | `main.py` 组装依赖，`args.py` 解析参数+配置 |
| `tui/` | 终端 UI | TUI 生命周期、输入处理、渲染、REPL 命令、主题（结构不变） |
| `server/` | Server 模式 | JSONL 协议 server + stdio/websocket/unix 三种 transport（结构不变） |
| `runtime/` | **Agent Runtime 内核** | `agent.py` 状态容器、`loop.py` 主循环、`compressor.py` 压缩、`events.py` 事件 |
| `backend/` | 模型后端 | `base.py` 接口 + `anthropic.py` / `openai.py` 策略实现，不依赖 runtime |
| `capabilities/` | 能力模块 | 7 个子模块：tools / mcp / skills / hooks / memory / sandbox / permissions |
| `context/` | 上下文构建 | `builder.py` 组装 system prompt + startup context、`sources.py` 数据源 |
| `models.py` | 模型元数据 | 上下文窗口、thinking 支持、输出 token、重试策略（被各方共用） |
| `protocol/` | JSONL 协议 | 消息类型、请求/响应、dispatcher（结构不变） |
| `session/` | 会话持久化 | event store、artifacts、snapshots（结构不变） |

### capabilities/ 子模块

```
capabilities/
├── tools/         # 5 文件：types, builtin, registry, runtime
├── mcp/           # 6 文件：types, config, manager, connection, transport, resources
├── skills/        # 4 文件：types, registry, runtime, prompt
├── hooks/         # 3 文件：types, config, runner
├── memory/        # 4 文件：types, store, retrieval, consolidation
├── sandbox/       # 6 文件：types, config, manager, backend, bwrap_backend, microsandbox_backend
└── permissions/   # 4 文件：policy, rules, workspace, shell
```

每个子模块遵循共同模板：`types.py`（数据模型）+ 引擎文件（按独立变更原因拆 N 个）。`types.py` 短不是问题——一致性降低学习成本。

## 代码划分原则

三条原则，决定"文件该拆还是该合"、"新代码放哪"：

1. **独立变更原因** — 改 A 时是否几乎总得同时改 B？是则合并，否则可以分开。不按"有没有关系"拆分，按"会不会一起改"拆分。
2. **能力模块保持共同模板** — 每个 `capabilities/<name>/` 的结构约定一致，读者看一个就能推断其他。
3. **依赖方向单向** — `backend/` 不引用 `runtime/`，`capabilities/` 不引用 `runtime/`，子模块间不互相引用（tools → hooks 例外）。

## 关键设计决策

### Agent 是纯状态容器，不是 God-class

`runtime/agent.py` 只持有字段和简单 getter/setter，不包含对话循环、API 调用、压缩策略。这些行为分别由 `runtime/loop.py`（循环）、`backend/`（API 调用）、`runtime/compressor.py`（压缩）实现。

子 Agent fork 复用同一个 `Agent` 类，通过 `custom_system_prompt` 和 `custom_tools` 定制行为边界。

### Backend 是策略类，不是 Mixin

`backend/base.py` 定义 `Backend` 抽象类（`call()` + `supports_thinking()`），`anthropic.py` 和 `openai.py` 各自实现。`loop.py` 通过接口调用，不区分后端。

新增模型厂商只需加一个文件，不需要改 runtime。

### 双消息历史刻意不统一

`_anthropic_messages` 和 `_openai_messages` 分开存储。Anthropic 的 tool_use/tool_result block 和 OpenAI 的 function call/role:tool 格式差异太大，强行统一抽象会增加复杂度。`Agent` 的公开方法（`add_user_message`、`add_assistant_message`、`add_tool_results`、`append_user_context`）在内部根据 `config.use_openai` 路由到正确的列表。

### 事件用工厂函数而非子类

```python
# 工厂函数，不用 isinstance 判断链
ToolCallStarted(call)    # → RuntimeEvent(type="tool.started", payload={...})
LoopFinished("stop")     # → RuntimeEvent(type="turn.finished", payload={...})
```

### capabilities 不抽象统一基类

每个 capability 的接口天然不同——tools 需要 registry + runtime、skills 需要 registry + runtime + prompt、hooks 只需要 runner + config。抽象出统一的 `Capability` 基类只会增加约束而没有任何实际收益。

## 架构规则

### 工具系统

- 普通工具无状态。Schema 在 `tools/builtin.py`，实现同文件，执行元数据通过 `ToolRegistry` 管理。
- 需要访问 Agent 状态的"元工具"（agent、skill、tool_search、mcp 工具）通过 `ToolContext.agent` 动态调用，不静态 import Agent。
- 工具错误返回 `ToolResult(..., is_error=True)` 或 `"Error: ..."` 字符串，不要打断主循环。
- **先读后改**：已有文件在 `write_file`/`edit_file` 前必须先 `read_file`。mtime 变更后必须重新读取。

### 权限系统

- 权限检查顺序：protected path → workspace 边界 → deny 规则 → confirm。这个顺序不能被 `bypassPermissions` 绕过。
- Shell 命令必须通过 sandbox manager 执行，不允许裸 `subprocess.run(shell=True)`。

### 消息格式不变量

- Anthropic：tool_use block 必须收到匹配的 tool_result block。
- OpenAI：tool call 必须收到匹配的 `role: tool` 消息。
- Compact 只能在安全的对话边界执行（不打断未完成的 tool-call/result 交换）。

### 子 Agent 约束

- `explore`、`plan` 类型只拿到只读工具（read_file、list_files、grep_search）。
- 所有子 Agent 不应拿到 `agent` 工具，避免递归创建。

### Hook 规则

- `Stop` hook 可通过追加 user context 强制主循环再跑一轮。
- 项目级 hooks 仅在 `NANO_CODE_TRUST_PROJECT_HOOKS=1` 时加载。

### MCP 规则

- MCP 工具通过 `ToolRegistry.add_many(..., origin="mcp")` 注册，按 `mcp__server__tool` 命名路由。
- 主循环不为 MCP 写特殊分支。

### Sandbox 规则

- Sandbox 是可选能力。未安装 `microsandbox` 时本地执行可用。
- `run_shell` 的所有执行路径都要求显式传入 sandbox backend。

## 常见任务指南

### 新增模型厂商

1. `models.py` — 添加模型元数据（上下文窗口等）
2. `backend/xxx.py` — 实现 `Backend` 接口
3. `backend/__init__.py` — `create_backend()` 增加分支
4. `cli/args.py` — 如有新的 API key 环境变量

不改 `runtime/` 和 `capabilities/`。

### 新增内置工具

1. `capabilities/tools/types.py` — 如有新的工具属性字段
2. `capabilities/tools/builtin.py` — 添加 schema 定义 + 实现函数
3. `capabilities/tools/registry.py` — 如需特殊注册逻辑（极少情况）

### 新增一个 capability

1. 创建 `capabilities/<name>/` 目录
2. `types.py` — 定义本 capability 的数据结构
3. 引擎文件 — 按变更原因拆分
4. `runtime/agent.py` — Agent 的 `__init__` 中实例化

### 新增 CLI 参数

1. `cli/args.py` — `parse_args()` 添加参数，`resolve_runtime_config()` 添加映射
2. `runtime/agent.py` — `RuntimeConfig` 如有新字段

## 硬性约束

- **外部接口不变**：`RuntimeEvent` JSON 格式、CLI 参数名、Server JSONL 协议、工具 schema 定义
- **会话格式不变**：`session/*.json` 存储格式，确保已有会话可 resume
- **Python >= 3.10**，依赖不新增
- **子 Agent fork 机制不变**：`Agent(..., is_sub_agent=True) + run_once()`

## 文件组织约定

每个 `.py` 文件头 10-20 行是模块文档字符串，说明：文件职责、关键依赖、变更原因（什么需求会改这个文件）。

```python
"""Agent 状态容器。

本模块是 Agent 的数据面。持有状态字段，不实现对话循环/API 调用/压缩。

变更原因：
  - 加新状态字段 → 改 __init__
  - 改消息历史操作 → 改 add_*/append_* 方法
  - 加新能力模块 → 改 __init__
"""
```

- 导入顺序：`from __future__ import annotations` → 标准库 → 第三方 → 项目内部（相对导入）
- 类型标注：公开方法必须标注参数和返回类型
- 编码风格：`ruff` 标准（行宽 120，双引号，isort）

## 不要做什么

- 不引入新的依赖或框架（DI 容器、事件总线、ORM、CLI 框架）
- 不为"将来可能的扩展"创建抽象——只对"当前已存在的变更原因"拆分模块
- 不修改生成缓存（`__pycache__/`、`*.egg-info/`）
- 不跨模块移动职责（没有明确理由时）
- 不强求统一双后端消息格式

## 测试要求

- 修改源码后运行相关 unittest 模块
- 涉及 runtime、tools、permissions、hooks、sandbox、MCP、session、prompt、memory、skills、event loop 的改动还要跑 `test/v1`
- 单元测试不依赖真实 API / MCP subprocess / microsandbox 容器
- 采用 miniconda 的 medicalgpt 环境
