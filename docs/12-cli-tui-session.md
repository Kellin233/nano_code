# CLI / TUI / 会话

## 1. 为什么需要这三层

从用户敲 `nanocode "修 bug"` 到 Agent 开始干活——需要解析参数、组装依赖、选交互模式、持久化会话。这三个模块是"外层"——不包含对话逻辑，只负责启动和渲染。

## 2. 核心概念

### 2.1 CLI → RuntimeConfig → 三个对象

`parse_args()` → `resolve_runtime_config()` → `Agent(config)` + `create_backend(config)` + `AgentLoop(agent, backend)` → 有 prompt？一次性模式 : TUI 模式。

CLI 层不包含对话逻辑——全部委托给 runtime/。

### 2.2 三种消费端，一个事件流

`AgentLoop.run()` 产 `AsyncIterator[RuntimeEvent]`。一次性：`_render_event()` 打印。TUI：`TuiApp._chat()` Rich 渲染。Server：JSONL 转发。

### 2.3 会话持久化

`SessionEventStore` append-only JSONL。`ArtifactStore` 大结果落盘（>30KB）。`--resume` 加载最近会话。

## 3. 总体设计

```
cli/     → args.py（参数解析）+ main.py（组装+启动）
tui/     → app.py（REPL）+ input.py（prompt_toolkit）+ renderer.py（Rich）
session/ → __init__.py（save/load/list）+ event_store.py（JSONL）+ artifacts.py（大结果）
```

## 4. 详细设计

**`cli/args.py`**：argparse 定义 + `resolve_permission_mode()` + `resolve_runtime_config()`（环境变量合并）。

**`cli/main.py`**：`main()` 组装三对象，选模式。`_run_once()` 阻塞确认 + 事件渲染。`_run_interactive()` 委托 TuiApp。

**`tui/app.py`**：`TuiApp.run()` 交互循环——`_read_line()` → `_handle_line()` → command 分发 → `_chat()` 驱动 AgentLoop。

**`session/`**：`save_session()`/`load_session()` JSON 读写。`SessionEventStore` JSONL 追加 + `replay()`。`ArtifactStore` 大结果落盘。

## 5. 设计决策

### 为什么 CLI 层无对话逻辑

入口包含对话→加参数要理解循环，改渲染要改入口。拆成 cli/(组装)→runtime/(执行)→tui/(渲染)，变更原因独立。

### 为什么三种模式共用 AgentLoop

对话逻辑完全一样，只是消费端不同。共用保证行为一致——不会"一次性模式和 TUI 结果不同"。

## 6. 面试考点

**Q: 为什么三种模式共用 AgentLoop？** 行为一致。消费端差异不影响对话逻辑。

## 7. 代码导读

**关键代码**：`cli/args.py` resolve_runtime_config()、`cli/main.py` main() + _run_once()、`tui/app.py` TuiApp.run() + _chat()、`session/event_store.py` SessionEventStore。
