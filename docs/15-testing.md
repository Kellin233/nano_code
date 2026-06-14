# 测试指南

## 1. 当前状态

测试仍放在历史目录名下：

```
test/runtime/
test/backend/
test/capabilities/
test/context/
test/cli/
test/tui/
```

这些目录名不完全等于当前源码包名，但测试 import 已迁移到新架构路径，例如：

- `nanocode.agent.*`
- `nanocode.agent.harness.*`
- `nanocode.providers.*`
- `nanocode.cli.core.*`
- `nanocode.cli.session`

后续可以重命名测试目录，但这不是运行正确性的前置条件。

## 2. 推荐命令

当前测试使用标准库 `unittest` 组织，`pyproject.toml` 也保留了 pytest 配置以便兼容运行。日常验证优先使用：

```bash
ruff check src test
PYTHONPATH=src python -m unittest discover -s test
```

如果需要用 pytest 跑同一批测试，也可以执行：

```bash
PYTHONPATH=src python -m pytest -q
```

编译检查：

```bash
python -m compileall -q src
```

安装和入口 smoke：

```bash
python -m pip install -e . --no-deps
nanocode --help
```

## 3. 测试分层

| 层 | 测什么 |
|----|--------|
| Agent core | Agent 状态、消息格式、预算、AgentLoop 状态机、RuntimeEvent |
| Harness | Compressor、context builder、hooks、permissions、persistence/session log/run store |
| Providers | BackendResponse、流式解析、schema 转换、thinking mode |
| cli/core | tools、sandbox、skills、memory、MCP、subagents、extensions |
| CLI/TUI/Server | 参数解析、REPL 命令、事件渲染、protocol |

## 4. Mock 策略

- 用 FakeBackend 代替真实 API。
- 用 FakeSandbox 代替真实 shell 隔离。
- 用临时目录隔离文件读写。
- 测 AgentLoop 时注入 fake `execute_tools`，不要让 core 测试依赖 ToolRuntime。
- 测 ToolRuntime 时构造最小 `ToolContext`，不要创建完整 TUI。

原则是只 mock 外部依赖，内部组件尽量用真实对象组合。

## 5. 架构边界测试

建议保留类似检查：

```bash
rg -n "from .*cli|from .*tui|from .*providers|import anthropic|import openai|open\(" \
  src/agent/agent.py src/agent/loop.py src/agent/events.py src/agent/types.py src/agent/models.py src/agent/budget.py

rg -n "from .*cli|from .*tui|from .*providers" src/agent/harness -g '*.py'
```

第一条用于确保 Agent core 没有反向依赖或 SDK import。第二条用于确保 harness 不依赖表现层和 provider。

## 6. 新增测试 Checklist

- 改 `agent/`：优先补 `test/runtime` 中 Agent/Loop 相关测试。
- 改 `agent/harness/context`：补 `test/context`。
- 改 `agent/harness/permissions/hooks/compressor`：补对应 runtime/capabilities 历史目录测试。
- 改 `providers/`：补 `test/backend`。
- 改 `cli/core/tools`：补 `test/capabilities/test_tools*`。
- 改 `cli/session.py`：补集成测试，验证装配和回调桥接。
- 改 `cli/core/extensions`：补注册工具、事件订阅、错误隔离测试。
