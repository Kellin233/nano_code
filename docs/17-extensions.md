# 扩展系统

## 1. 为什么需要 Extension

Hook 适合外部进程级拦截，但不适合注册新工具或订阅进程内事件。Extension 是应用层的进程内 Python 扩展面，可以注册工具、注册命令、订阅运行时事件。

Extension 位于 `cli/core/extensions/`。Agent core 不 import extension，也不知道 extension 是否存在。

## 2. 文件结构

```
cli/core/extensions/
├── __init__.py
├── api.py       # ExtensionAPI，传给插件的对象
├── loader.py    # 扫描 .nanocode/extensions/*.py 并调用 register(api)
└── runner.py    # ExtensionRunner，事件分发和错误隔离
```

## 3. 加载模型

`AgentSession.__init__` 中：

```
extension_runner = ExtensionRunner()
extension_api = ExtensionAPI(extension_runner, tool_registry)
loaded_extensions = load_extensions(extension_api)
```

默认扫描：

```
{cwd}/.nanocode/extensions/*.py
```

每个扩展文件如果有 `register(api)` 函数，就会被调用。

## 4. API

ExtensionAPI 当前提供：

```python
api.register_tool(definition, handler, ...)
api.on(event, handler)
api.register_command(name, handler)
```

`register_tool()` 把扩展工具注册进同一个 `ToolRegistry`。后续执行路径和内置工具一致，都会经过 ToolRuntime 的校验、hooks、权限、确认和大结果持久化。

## 5. 事件

ExtensionRunner 支持事件订阅：

- `agent_start`
- `agent_end`
- `turn_start`
- `turn_end`
- `before_tool_call`
- `after_tool_call`

生命周期事件由 AgentLoop 触发，经 Agent 回调槽位桥接到 ExtensionRunner。工具事件由 ToolRuntime 在执行管线中触发。

## 6. Hook vs Extension

| 维度 | Hook | Extension |
|------|------|-----------|
| 位置 | `agent/runtime_management/hooks/` | `cli/core/extensions/` |
| 运行方式 | 外部进程 | 进程内 Python |
| 主要能力 | deny/allow/modify/append_context | 注册工具、命令、事件订阅 |
| 安全模型 | 用户配置命令，项目 hooks 默认不信任 | 代码直接运行，必须信任扩展 |
| Agent core 是否感知 | 否 | 否 |

## 7. 示例

```python
from nanocode.agent.types import ToolResult


def register(api):
    api.register_tool(
        {
            "name": "hello_extension",
            "description": "Return a greeting from an extension.",
            "input_schema": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
            },
        },
        hello,
        read_only=True,
        concurrency_safe=True,
    )


async def hello(inp, ctx):
    name = inp.get("name") or "world"
    return ToolResult(f"hello {name}")
```

## 8. 错误隔离

Extension handler 抛异常时，`ExtensionRunner` 会记录到 `runner.errors`，不会让 AgentLoop 直接崩溃。扩展工具自身执行错误仍通过 `ToolResult(is_error=True)` 返回。

## 9. 设计决策

### 为什么 Extension 在 cli/core

Extension 是“Agent 能做什么”的扩展面，属于应用层能力市场，不属于 core 或 Runtime Management。

### 为什么通过 AgentSession 桥接

Agent core 只暴露回调槽位。`AgentSession` 创建 ExtensionRunner，并把 runner 方法填入 Agent 回调和 ToolRuntime before/after hook。这样 core 不依赖扩展系统。

### 为什么先做 Python .py 加载

项目规模是单用户 CLI，Python `register(api)` 约定足够简单。复杂的插件包管理、签名和市场机制可以后续再加。
