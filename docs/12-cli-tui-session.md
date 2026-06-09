# CLI / TUI / 会话

## 1. 为什么需要这三层

从用户敲下 `nanocode "修 bug"` 到 Agent 开始干活——中间要处理三件事：解析参数、组装依赖、选交互模式。会话持久化让用户 `/exit` 后 `--resume` 继续。

三个模块都是"外层"——不包含对话逻辑。

## 2. 核心概念

### 2.1 CLI → RuntimeConfig → 三对象

`parse_args()` 解析 CLI 参数→`resolve_runtime_config()` 合并环境变量→`Agent(config)` + `create_backend(config)` + `AgentLoop(agent, backend)`→有 prompt？一次性模式 : TUI 模式。

### 2.2 三种消费端，一个事件流

`AgentLoop.run()` 产出 `RuntimeEvent` 流。一次性：`_render_event()` 直接打印。TUI：`TuiApp._chat()` Rich 渲染。Server：`event.to_dict()` JSONL 转发。

### 2.3 会话持久化

`SessionEventStore`：append-only JSONL 文件，`replay()` 恢复事件列表。`ArtifactStore`：大结果（>30KB）落盘。`save_session()`/`load_session()`：保存/恢复消息历史 JSON。

## 3. 总体设计

```
cli/     — args.py（参数解析+环境变量合并）+ main.py（组装+启动）
tui/     — app.py（REPL 生命周期）+ input.py（prompt_toolkit）+ renderer.py（Rich）+ commands.py + state.py + theme.py
session/ — __init__.py（save/load/list）+ event_store.py（JSONL）+ artifacts.py（大结果）
```

## 4. 详细设计

**`cli/args.py`**：argparse 定义所有参数。`resolve_permission_mode()` yolo>accept-edits>dont-ask>default。`resolve_runtime_config()` 合并 CLI+环境变量→RuntimeConfig。

**`cli/main.py`**：`main()` 组装 Agent+Backend+AgentLoop。`_run_once()` 阻塞确认回调+事件渲染+session resume。`_run_interactive()` 委托 TuiApp。

**`tui/app.py`**：`TuiApp.run()` 交互循环——read_line→handle_line→command 分发或 `_chat(prompt)`→驱动 AgentLoop+渲染事件。

**`session/`**：JSONL 追加+replay+next_seq。ArtifactStore.write_text() 落盘大结果。

## 5. 设计决策

### 为什么 CLI 层不含对话逻辑

入口含对话→加参数要理解循环，改渲染要改入口。拆成 cli/（组装）→runtime/（执行）→tui/（渲染）变更原因独立。

### 为什么三种模式共用 AgentLoop

对话逻辑完全一样——只是消费端不同。共用保证行为一致。

### 为什么会话用 JSONL

纯文本、可 grep、可 tail。会话数量少，不需要 SQLite。

## 6. 面试考点

**Q: 为什么三种模式共用 AgentLoop？** 保证行为一致——不会"一次性模式和 TUI 结果不同"。

**Q: 为什么 JSONL 而非 SQLite？** 纯文本可审计、适合少量会话。SQLite 更适合大量查询。

## 7. 代码导读

**关键行号**：`cli/args.py` resolve_runtime_config()、`cli/main.py` main()+_run_once()、`tui/app.py` TuiApp.run()+_chat()、`session/event_store.py` replay()。
