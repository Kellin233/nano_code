# CLI / TUI / 会话

## 为什么需要这三层

从用户敲下 `nanocode "修 bug"` 到 Agent 开始干活——中间有三件事要处理：解析参数（CLI）、组装依赖（CLI）、选交互模式（TUI 还是 headless）。会话持久化让用户 `/exit` 之后下次 `--resume` 能继续。

这三个模块都是"外层"——它们不包含对话逻辑，只负责把 Agent 启动起来、把事件渲染出来、把状态存下来。

## 核心概念

### CLI：参数 → RuntimeConfig → 三个对象

```
parse_args() → resolve_runtime_config()
    → Agent(config)                     # 状态容器
    → create_backend(config)            # 模型后端
    → AgentLoop(agent, backend)         # 主循环
    → 有 prompt？一次性模式 : TUI 模式
```

CLI 层不包含任何对话逻辑——全部委托给 runtime/。

### 三种消费端，一个事件流

`AgentLoop.run()` 产出 `AsyncIterator[RuntimeEvent]`。三种消费端各自消费：

- 一次性模式：`_render_event()` 直接打印
- TUI 模式：`TuiApp._chat()` 驱动 Rich 渲染
- Server 模式：JSONL 转发

### 会话持久化

`SessionEventStore` 用 append-only JSONL 存储每轮的事件。`ArtifactStore` 处理大结果落盘（>30KB）。`--resume` 加载最近会话的 `{session_id}.json`，恢复消息历史。

## 设计决策

### 为什么 CLI 层不包含对话逻辑

入口层如果包含对话逻辑——加一个参数就要理解整个循环，改 TUI 渲染要改入口文件。拆成 cli/（组装）→ runtime/（执行）→ tui/（渲染），每个文件的变更原因独立。

### 为什么三种模式共用 AgentLoop

一次性、TUI、Server 只是消费端不同——对话逻辑完全一样。共用 AgentLoop 保证行为一致——不会出现"一次性模式和 TUI 模式执行结果不同"的 bug。

### 为什么会话用 JSONL 而非 SQLite

JSONL 是纯文本——可 grep、可 tail、可手动编辑。会话文件数量少（几十个），不需要索引。SQLite 更适合大量查询——当前场景不需要。

## 代码走读

**`cli/args.py`**：argparse 定义 + `resolve_permission_mode()` + `resolve_runtime_config()`（环境变量合并）。

**`cli/main.py`**：`main()` 组装 Agent + Backend + AgentLoop，选模式。`_run_once()` 阻塞式确认回调 + 事件渲染。`_run_interactive()` 委托 `TuiApp`。

**`tui/app.py`**：`TuiApp.run()` 交互循环——`_read_line()` → `_handle_line()` → command 分发 → `_chat()` 驱动 AgentLoop。

**`session/`**：`save_session()` / `load_session()` JSON 读写。`SessionEventStore` JSONL 追加 + `replay()`。`ArtifactStore` 大结果落盘。

## 面试考点

**Q: 为什么三种运行模式共用 AgentLoop？**

对话逻辑完全一样——只是消费端不同。共用保证行为一致，不会出现"一次性模式和 TUI 模式结果不同"。
