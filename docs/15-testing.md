# 测试指南

## 1. 为什么需要测试

重构 55 个源文件、改 7 个能力模块——没有测试就是瞎改。策略：单元测试不依赖外部服务（API/MCP/sandbox），集成测试用 FakeBackend 和 FakeSandbox。

## 2. 核心概念

### 2.1 测试目录与源码对应

`test/runtime/`→Agent+Loop+Compressor、`test/backend/`→Backend 接口、`test/cli/`→CLI 参数、`test/context/`→提示词、`test/capabilities/`→7 个能力模块、`test/tui/`→TUI。

### 2.2 Mock 策略

**FakeBackend**：预设 BackendResponse，验证调用次数和参数。**FakeSandbox**：记录 shell 命令调用，返回预设输出。**patch Agent.run_once**：模拟子 Agent 行为。

单元测试用真实内部组件（Agent、ToolRegistry、Compressor），只 mock 外部依赖。过度 mock 会让测试只验证"mock 被调用了"。

## 3. 常见失败

| 失败 | 原因 |
|------|------|
| `ModuleNotFoundError: .tools.base` | 旧 import——base 已合并到 types |
| `TypeError: Agent() got unexpected keyword` | 旧构造器——改 RuntimeConfig |
| grep_search "No matches" | 系统 grep 环境问题 |

## 4. 面试考点

**Q: 为什么不 mock 所有东西？** 只用 mock 替代外部依赖。内部组件用真实实例——测试的是真实交互而非 mock 调 mock。

**5. 代码导读**：运行 `python3 -m unittest discover -s test -v`。加测试：改什么文件就在对应 test/ 目录加 `test_*.py`。
