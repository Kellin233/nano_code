# 代码导读

## 为什么需要这篇导读

55 个源文件——从哪开始读？这个项目没有自动生成的文档，但这篇导读告诉你怎么用最短路径理解整个系统。

## 推荐阅读顺序

跟着一条请求走：**入口 → 内核 → 模型 → 工具 → 返回**。

```
1. cli/main.py          → main() 入口，理解组装逻辑
2. runtime/agent.py      → Agent 状态容器，知道有哪些状态
3. runtime/loop.py       → AgentLoop.run()，理解主循环
4. backend/anthropic.py  → 模型怎么被调用的
5. capabilities/tools/   → 工具怎么被注册和执行
6. runtime/compressor.py → 上下文怎么压缩
7. context/builder.py    → 提示词和附件怎么构建
```

读完这 7 个文件，你理解了 80% 的核心流程。剩下的 capabilities 子模块是独立的能力模块——按兴趣读。

## 关键文件标注

| 文件 | 复杂度 | 说明 |
|------|:--:|------|
| `runtime/agent.py` | ★★★★ | 最大文件（~580 行）。Agent 全部状态 + 消息操作 + 子 Agent fork。阅读起点 |
| `runtime/loop.py` | ★★★ | 主循环（~326 行）。后端无关的核心逻辑。理解数据流必读 |
| `runtime/compressor.py` | ★★★ | 三层压缩（~264 行）。compressor 直接读写 Agent 私有字段 |
| `backend/anthropic.py` | ★★★ | Anthropic 流式解析（~160 行） |
| `capabilities/tools/types.py` | ★★★ | 工具数据模型 + 所有常量 |
| `capabilities/tools/registry.py` | ★★★ | ToolRegistry 注册/查找/激活 |
| `capabilities/tools/runtime.py` | ★★★ | ToolRuntime 执行管线 |
| `context/sources.py` | ★★★★ | CLAUDE.md 加载链 + Git + frontmatter |

## 常见修改路径

**加一个新工具**：`capabilities/tools/builtin.py`——在 `BUILTIN_TOOL_DEFINITIONS` 加 schema，下方加实现。不改其他文件。

**加一个新模型厂商**：`models.py` 加模型元数据，新建 `backend/xxx.py` 实现 `Backend` 接口。`AgentLoop` 不改。

**加一个 CLI 参数**：`cli/args.py` 的 `parse_args()` + `resolve_runtime_config()`。如果参数是配置字段，`runtime/agent.py` 的 `RuntimeConfig` 加字段。

**加一个 capability**：新建 `capabilities/<name>/` 目录，`types.py` + 引擎文件，`runtime/agent.py` 的 `__init__` 中实例化。

## 设计模式速查

| 模式 | 位置 | 说明 |
|------|------|------|
| 策略模式 | `backend/` | AnthropicBackend/OpenAIBackend |
| 工厂函数 | `backend/__init__.py`、`runtime/events.py` | create_backend()、事件工厂 |
| 事件流 | `runtime/loop.py` | AsyncIterator[RuntimeEvent] |
| 注册表模式 | `capabilities/tools/registry.py` | ToolRegistry |

## 面试考点

**Q: 如果只能读 3 个文件理解项目，读哪 3 个？**

`cli/main.py`（入口 + 组装）、`runtime/loop.py`（主循环）、`capabilities/tools/runtime.py`（工具执行）。这三文件覆盖了从用户输入到工具执行返回的完整路径。
