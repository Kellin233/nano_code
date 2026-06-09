# 测试指南

## 1. 为什么需要测试

重构 55 个源文件、改 7 个能力模块——没有测试就是瞎改。策略：单元测试不依赖外部服务（API、MCP、sandbox），集成测试用 FakeBackend 和 FakeSandbox 模拟。

## 2. 核心概念

### 2.1 测试目录 → 源码目录 一一对应

`test/runtime/`→Agent+Loop+Compressor | `test/backend/`→Backend | `test/cli/`→CLI 参数 | `test/context/`→提示词 | `test/capabilities/`→7 个能力模块 | `test/tui/`→TUI。

### 2.2 测试分层

**单元测试**：不依赖 API/文件系统/外部服务。测试 Agent 状态操作、ToolRegistry 注册查找、Compressor 压缩逻辑。Mock 和 Fake 替代真实依赖。

**集成测试**：需要临时文件或 FakeBackend。测试 AgentLoop 完整流程、ToolRuntime 执行管线、Hooks 交互。

### 2.3 Mock 策略

**FakeBackend**：返回预设 BackendResponse，验证调用次数和参数。**FakeSandbox**：记录 shell 命令调用，返回预设输出。**patch Agent.run_once**：模拟子 Agent 返回。

内部组件用真实实例，只 mock 外部依赖。过度 mock 会让测试只验证"mock 被调用了"而非"系统行为正确"。

## 3. 常见失败

| 失败 | 原因 |
|------|------|
| `ModuleNotFoundError: .tools.base` | 旧 import——base 已合并到 types |
| `TypeError: Agent() got unexpected keyword` | 旧构造器——改 RuntimeConfig |
| `grep_search` 返回 "No matches" | 系统 grep 环境问题 |

## 4. 新增测试 Checklist

改了什么文件→对应 test 目录加 `test_*.py`。加工具→`test/capabilities/test_tools.py`。加 CLI 参数→`test/cli/test_args.py`。改 Agent→`test/runtime/test_agent.py`。

## 5. 面试考点

**Q: 为什么不 mock 所有东西？** 只 mock 外部依赖。内部组件用真实实例——测试真实交互。过度 mock→测试只验证 mock 调用，不验证行为。

## 6. 代码导读

```bash
python3 -m unittest discover -s test -v          # 全部测试
python3 -m unittest discover -s test/runtime -v  # 特定模块
```
