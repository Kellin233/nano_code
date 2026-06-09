# 代码导读

## 1. 推荐阅读顺序

跟着一条请求：`cli/main.py`（入口+组装）→`runtime/agent.py`（状态）→`runtime/loop.py`（循环）→`backend/anthropic.py`（模型调用）→`capabilities/tools/`（工具）→`runtime/compressor.py`（压缩）→`context/builder.py`（提示词）。读 7 个文件理解 80% 核心流程。

## 2. 关键文件

| 文件 | 行数 | 为什么重要 |
|------|:--:|------|
| runtime/agent.py | ~590 | 最大文件，Agent 全部状态+消息操作 |
| runtime/loop.py | ~326 | 主循环，后端无关核心逻辑 |
| runtime/compressor.py | ~264 | 三层压缩+compact |
| backend/anthropic.py | ~160 | Anthropic 流式解析 |
| capabilities/tools/builtin.py | ~440 | 12 个内置工具 schema+实现 |
| capabilities/tools/runtime.py | ~250 | 工具执行管线 |
| capabilities/tools/registry.py | ~330 | ToolRegistry 注册/激活 |
| context/sources.py | ~320 | CLAUDE.md 加载链 |

## 3. 修改路径

**加工具**：`builtin.py` 加 schema+实现。**加模型厂商**：`models.py` 加元数据+新建 `backend/xxx.py`。**加 CLI 参数**：`cli/args.py` 加参数+映射。**加 capability**：新建 `capabilities/<name>/`+`agent.py` 中实例化。

## 4. 设计模式速查

| 模式 | 位置 |
|------|------|
| 策略模式 | backend/: AnthropicBackend/OpenAIBackend |
| 工厂函数 | backend/__init__.py、runtime/events.py |
| 事件流 | runtime/loop.py: AsyncIterator[RuntimeEvent] |
| 注册表模式 | capabilities/tools/registry.py: ToolRegistry |

## 5. 面试考点

**Q: 只读 3 个文件理解项目？** `cli/main.py`（入口）、`runtime/loop.py`（循环）、`capabilities/tools/runtime.py`（工具执行）。覆盖从输入到执行的完整路径。
