# 功能测试指南

## 测试目录结构

测试目录与源码模块一一对应：

```
test/
├── runtime/           # Agent、Loop、Compressor、SubAgent、架构
├── backend/           # Backend 接口 + 工厂
├── cli/               # CLI 参数解析 + 集成
├── context/           # System prompt、附件渲染、数据源
├── capabilities/      # tools、skills、hooks、sandbox、memory、mcp、permissions
└── tui/               # TUI 交互
```

## 测试运行

```bash
# 全部测试
python3 -m unittest discover -s test -v

# 特定模块
python3 -m unittest discover -s test/runtime -v
python3 -m unittest discover -s test/capabilities -v

# 编译检查（先于测试运行）
python3 -m compileall src test
```

## 测试分层

| 层 | 特征 | 示例 |
|:--:|------|------|
| **单元测试** | 不依赖真实 API/文件系统/外部服务 | `test_agent.py`——测试 Agent 状态操作 |
| **集成测试** | 需要临时文件或 mock Backend | `test_loop.py`——用 FakeBackend 驱动 AgentLoop |

**单元测试不依赖**：真实 Anthropic/OpenAI API、MCP subprocess、microsandbox 容器。这些依赖在 CI 环境中不可用。

## Mock 策略

### Mock Backend

```python
class FakeBackend(Backend):
    def __init__(self, text="", tool_calls=None, usage=None):
        self._text = text
        self._tool_calls = tool_calls or []
        self._usage = usage or TokenUsage(3, 2)
    
    async def call(self, **kwargs):
        return BackendResponse(text=self._text, 
                               tool_calls=self._tool_calls, 
                               usage=self._usage)
```

### Mock Sandbox

```python
class FakeShell:
    def __init__(self, output="ok"):
        self.calls = []
    async def run_shell(self, command, timeout_ms, cwd):
        self.calls.append((command, timeout_ms, cwd))
        return self.output
```

### Mock Agent.run_once

```python
with patch.object(Agent, "run_once", return_value={"text": "done", "tokens": ...}):
    results = asyncio.run(orchestrator.dispatch(tasks))
```

## 常见测试失败原因

| 失败类型 | 常见原因 |
|---------|---------|
| `ModuleNotFoundError` | import 路径过时（`.base`/`.constants`/`.definitions` 已合并为 `.types`/`.builtin`） |
| `AttributeError: 'Agent' has no attribute '_call_anthropic_stream'` | 测试使用了 Mixin 方法——应改用 `FakeBackend` |
| `TypeError: Agent() got unexpected keyword argument 'api_key'` | 使用了旧构造器——应改用 `Agent(RuntimeConfig(...))` |
| `grep_search` 返回 "No matches found" | 系统 grep 环境问题——非代码 bug |
| `ToolResult: requires a sandbox manager` | `run_shell` 测试需要 mock sandbox |

## 新增测试 Checklist

修改了什么代码，就在对应目录加测试：

| 改了什么 | 加测试到 |
|---------|---------|
| `runtime/agent.py` | `test/runtime/test_agent.py` |
| `runtime/loop.py` | `test/runtime/test_loop.py` |
| `runtime/compressor.py` | `test/runtime/test_compressor.py` |
| `backend/*.py` | `test/backend/test_backend.py` |
| `cli/args.py` | `test/cli/test_args.py` |
| `context/builder.py` | `test/context/test_builder.py` |
| `capabilities/tools/*.py` | `test/capabilities/test_tools*.py` |
| `capabilities/subagents/*.py` | `test/runtime/test_subagent.py` |
