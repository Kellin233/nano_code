# 测试指南

## 为什么需要测试

重构 55 个源文件、改 7 个能力模块、重新划分依赖方向——没有测试就是在瞎改。NanoCode 的测试策略是：单元测试不依赖任何外部服务（API、MCP server、microsandbox），集成测试用 FakeBackend 和 FakeSandbox 模拟。

## 核心概念

### 测试目录与源码一一对应

```
test/
├── runtime/           # Agent、Loop、Compressor、SubAgent
├── backend/           # Backend 接口 + 工厂
├── cli/               # CLI 参数解析
├── context/           # 提示词构建 + 数据源
├── capabilities/      # tools、skills、hooks、sandbox、memory、mcp
└── tui/               # TUI 交互
```

每个测试文件测试一个模块。改了什么代码，就在对应目录加测试。

### 测试分层

**单元测试**：不依赖真实 API/文件系统/外部服务。测试 Agent 状态操作、ToolRegistry 注册查找、Compressor 压缩逻辑。用 Mock 和 Fake 对象替代真实依赖。

**集成测试**：需要临时文件或 FakeBackend。测试 AgentLoop 完整流程、ToolRuntime 执行管线、Hooks 交互。FakeBackend 返回预设的 `BackendResponse`。

## Mock 策略

### FakeBackend

```python
class FakeBackend(Backend):
    def __init__(self, text="", tool_calls=None, usage=None):
        self._text = text
        self._tool_calls = tool_calls or []
    async def call(self, **kwargs):
        return BackendResponse(text=self._text, tool_calls=self._tool_calls, usage=self._usage)
```

### FakeSandbox

```python
class FakeShell:
    async def run_shell(self, command, timeout_ms, cwd):
        self.calls.append((command, timeout_ms, cwd))
        return "ok"
```

### 测试组织原则

- **每个模块一个测试文件**：`test/runtime/test_agent.py`、`test/capabilities/test_tools.py`
- **测试命名带描述**：`test_budget_exceeded_cost_limit` 而非 `test_budget_1`
- **setUp/tearDown 隔离临时文件**：用 `tempfile.TemporaryDirectory`

## 常见失败原因

| 失败 | 原因 |
|------|------|
| `ModuleNotFoundError: .tools.base` | 旧 import 路径——base.py 已合并到 types.py |
| `TypeError: Agent() got unexpected keyword 'api_key'` | 旧构造函数——应改为 `Agent(RuntimeConfig(api_key=...))` |
| `grep_search` 返回 "No matches found" | 系统 grep 环境问题——非代码 bug |

## 面试考点

**Q: 为什么不 mock 所有东西？**

只 mock 外部依赖（API、MCP、sandbox）。内部组件（Agent、ToolRegistry、Compressor）用真实实例——测试的是组件之间的真实交互。过度 mock 会让测试只能验证"mock 对象被调用了"，而不是"系统行为正确"。
