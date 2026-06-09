# CLI 与会话设计

## 目标

把 CLI 入口和会话持久化放在一起讲清楚：用户怎么启动 nanocode、参数怎么解析成 RuntimeConfig、三种运行模式怎么共用一个 AgentLoop、会话怎么保存和恢复。

## 代码流程

```
用户执行 nanocode [options] [prompt]
         │
    cli/args.py
    parse_args()
         │
         ├── 解析 CLI 参数（argparse）
         │   模型、权限、sandbox、预算、thinking
         │
    resolve_runtime_config(args)
         │
         ├── 解析权限模式（yolo > accept-edits > dont-ask > default）
         ├── 解析 API key/provider（环境变量 + CLI 参数合并）
         ├── 解析 sandbox config（profile → SandboxConfig）
         └── 返回 RuntimeConfig 实例
         │
    cli/main.py
    main()
         │
         ├── --help？ → 打印帮助文本，退出
         ├── --server stdio？ → asyncio.run(run_stdio_server())
         │
         ├── 组装依赖：
         │   Agent(config)          # Agent 状态容器
         │   create_backend(config)  # Backend 策略类（Anthropic/OpenAI）
         │   AgentLoop(agent, backend)  # 主对话循环
         │
         ├── 有 prompt？ → _run_once(loop, prompt)
         │   1. 设置确认回调
         │   2. 恢复会话（--resume）
         │   3. loop.run(prompt) 产出 RuntimeEvent 流
         │   4. _render_event 渲染到终端
         │   5. agent.shutdown()
         │
         └── 无 prompt？ → _run_interactive(agent, loop)
             1. 恢复会话（--resume）
             2. TuiApp(agent, loop).run()
             3. agent.shutdown()
```

## 总体设计

### CLI 三层入口

| 层 | 文件 | 职责 | 变更原因 |
|---|------|------|---------|
| 参数解析 | `cli/args.py` | argparse 定义 + 环境变量合并 → RuntimeConfig | 新增 CLI 参数时改这里 |
| 依赖组装 | `cli/main.py` | 创建 Agent + Backend + AgentLoop，选择运行模式 | 改启动流程时改这里 |
| 执行 | `runtime/` | AgentLoop.run() 驱动对话，TuiApp 或一次性消费事件 | CLI 层不关心执行细节 |

### 三种运行模式

| 模式 | 触发条件 | 消费端 | 说明 |
|------|---------|--------|------|
| 一次性 | `nanocode "prompt"` | `_run_once` 直接消费 | 非交互，执行完退出 |
| TUI 交互 | `nanocode`（无参数） | `TuiApp.run()` | 交互式 REPL |
| Server | `--server stdio` | JSONL 协议 | 供外部程序通过 stdin/stdout 调用 |

三种模式共用同一个 `AgentLoop.run(prompt)`——产出 RuntimeEvent 流，不同消费端各自渲染。

### 配置解析优先级

```
CLI 参数 > 环境变量 > 默认值

权限模式：
  --yolo → bypassPermissions
  --accept-edits → acceptEdits
  --dont-ask → dontAsk
  (无) → default

Provider 检测：
  OPENAI_API_KEY + OPENAI_BASE_URL 同时存在 → openai
  ANTHROPIC_API_KEY 存在 → anthropic
  OPENAI_API_KEY 单独存在 → openai

Sandbox profile：
  --sandbox <profile> 显式指定 → 直接使用
  (无) → 默认 workspace（Linux）或 local（非 Linux）
```

### 会话持久化

```
session/
├── __init__.py       # save_session / load_session / list_sessions
├── event_store.py    # SessionEventStore：append-only JSONL
├── artifacts.py      # ArtifactStore：大结果落盘
└── snapshots.py      # SnapshotStore：快照管理
```

**SessionEventStore**：append-only JSONL 文件，每行一个 RuntimeEvent 的 JSON 序列化。`replay()` 恢复为 RuntimeEvent 列表。`next_seq()` 返回下一个序列号。

**ArtifactStore**：工具结果超过 30KB 时，不放进消息历史，写入 artifact 文件，返回引用路径和预览。`write_text()` 返回 `{"path": "...", "size_bytes": N}`。

**会话恢复**：`save_session()` 保存消息历史（Anthropic/OpenAI 分开存）+ metadata。`load_session()` 读回。`--resume` 启动时加载最近一次会话。

## 详细设计

### `cli/args.py`

`parse_args()` 用标准库 argparse 定义所有 CLI 参数。`resolve_permission_mode(args)` 按优先级返回权限模式。`resolve_runtime_config(args)` 合并环境变量和 CLI 参数组装 RuntimeConfig。

### `cli/main.py`

`main()` 入口：解析参数 → 组装 Agent + Backend + AgentLoop → 选模式启动。`_run_once` 阻塞式确认回调 + 事件流渲染。`_run_interactive` 委托给 TuiApp。

### `session/`

存储位置 `~/.nanocode/sessions/`。`save_session` 写入 `{session_id}.json`。`load_session` 读取 JSON。`list_sessions` 扫描所有会话文件。Agent 的 `_auto_save()` 在每次 `LoopFinished("stop")` 后自动调用。

## 硬性约束

- CLI 入口不包含任何对话逻辑——全部委托给 runtime/
- 保持 argparse，不引入 click/typer
- API key 检查在启动时紧耦合——没有 key 立即退出
- Sandbox 的 `build_sandbox_config` 在 `resolve_runtime_config` 中调用，ValueError 由 `main()` 捕获
- 会话 JSON 保持 Anthropic/OpenAI 双消息历史分开存储

## 隐含要求

- 一次性模式必须能处理 Ctrl+C 中断
- `--resume` 兼容一次性模式和 TUI 模式
- 帮助文本中的 REPL 命令必须与实际支持的保持一致
- 配置解析错误不能抛出原始异常

## 不能做什么

- 不能在 CLI 层直接调用模型
- 不能在参数解析中执行 I/O（除读环境变量）
- 不能在 `resolve_runtime_config` 中启动 sandbox backend（sandbox 是懒初始化的）
- 不能用全局变量共享配置

## 可能踩坑的地方

### argparse 与 add_help=False

当前 `add_help=False`，手动处理 `--help`。原因是需要自定义帮助文本格式（加入 REPL 命令和示例）。如果后续改成 argparse 内置 help，要确保不丢失 REPL 命令说明。

### 环境变量优先级

`OPENAI_API_KEY + OPENAI_BASE_URL` 同时存在才判定为 OpenAI provider。如果用户只设了 `OPENAI_API_KEY` 没设 `OPENAI_BASE_URL`，且没有 `ANTHROPIC_API_KEY`，会报错退出。

### argparse 的 append action

`--sandbox-env` 和 `--sandbox-extra-write` 使用 `action="append"`，多次传参是追加而非覆盖。

### SessionEventStore 的 replay 性能

当前是全量读取整个 events.jsonl 文件。如果会话很长，`next_seq()` 会读全文件只为取最后一行。后续可以改为只读最后一行或维护内存计数器。

### 会话文件损坏

`load_session()` 对 JSON 解析异常做 `return None`。`list_sessions()` 跳过解析失败的元数据文件。损坏文件会静默跳过，用户可能不知道数据丢失。
