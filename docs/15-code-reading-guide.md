# 代码导读

## 推荐阅读顺序

跟着一次用户请求走完整个流程：

```
1. cli/main.py          → main() 入口，理解组装逻辑
2. cli/args.py          → parse_args() + resolve_runtime_config()
3. runtime/agent.py      → Agent 状态容器，理解有哪些状态字段
4. runtime/loop.py       → AgentLoop.run()，理解主循环
5. backend/anthropic.py  → 模型如何被调用（选一个后端）
6. capabilities/tools/   → 工具如何被注册和执行
7. runtime/compressor.py → 上下文如何压缩
8. context/builder.py    → 提示词和附件如何构建
```

读完这 8 个文件，你就理解了 80% 的核心流程。

## 关键文件标注

| 文件 | 复杂度 | 说明 |
|------|:--:|------|
| `cli/main.py` | ★★ | 入口 + 依赖组装，~170 行，阅读起点 |
| `cli/args.py` | ★★ | argparse 定义 + 配置解析 |
| `runtime/agent.py` | ★★★★ | 最大文件 ~580 行，Agent 全部状态 + 辅助方法 |
| `runtime/loop.py` | ★★★ | 主循环 ~300 行，后端无关的核心逻辑 |
| `runtime/compressor.py` | ★★★ | 三层压缩 ~260 行，含 compact 对话摘要 |
| `runtime/events.py` | ★ | 事件定义 + 工厂函数 |
| `runtime/thread.py` | ★★★ | RuntimeThread 公开入口，事件流管理 |
| `backend/base.py` | ★ | Backend 接口定义 |
| `backend/anthropic.py` | ★★★ | Anthropic 流式解析 ~160 行 |
| `backend/openai.py` | ★★ | OpenAI 流式解析 ~110 行 |
| `models.py` | ★★ | 模型元数据 + 重试策略 |
| `context/builder.py` | ★★★ | System prompt + 启动上下文 + 附件渲染 |
| `context/sources.py` | ★★★★ | CLAUDE.md 加载 + Git + frontmatter |
| `capabilities/tools/types.py` | ★★★ | 工具数据模型 + 所有常量 |
| `capabilities/tools/builtin.py` | ★★★ | 12 个内置工具 schema + 实现 |
| `capabilities/tools/registry.py` | ★★★ | ToolRegistry 注册/查找/激活 |
| `capabilities/tools/runtime.py` | ★★★ | ToolRuntime 执行管线 |
| `capabilities/subagents/__init__.py` | ★★ | 3 种内置类型 + 自定义发现 |
| `capabilities/subagents/orchestrator.py` | ★★ | 并行编排器 ~90 行 |

## 常见修改路径

### "我要加一个新工具"

1. `capabilities/tools/builtin.py`：在 `BUILTIN_TOOL_DEFINITIONS` 中添加 schema，在下方添加实现函数
2. 如果是并发的，加到 `CONCURRENCY_SAFE_BUILTIN_TOOLS`
3. 如果会编辑文件，加到 `EDIT_TOOL_NAMES`
4. 测试：`test/capabilities/test_tools.py`

### "我要加一个新模型厂商"

1. `models.py`：添加模型元数据
2. 新建 `backend/xxx.py`：实现 `Backend` 接口
3. `backend/__init__.py`：`create_backend()` 增加分支
4. 不改 `runtime/`

### "我要加一个 CLI 参数"

1. `cli/args.py`：`parse_args()` 添加参数 + `resolve_runtime_config()` 添加映射
2. 如果涉及新配置字段，`runtime/agent.py` 的 `RuntimeConfig` 增加字段
3. 测试：`test/cli/test_args.py`

### "我要加一个 capability"

1. 创建 `capabilities/<name>/` 目录
2. `types.py` + 引擎文件
3. `runtime/agent.py` 的 `__init__` 中实例化
4. 测试：`test/capabilities/`

## 设计模式速查

| 模式 | 位置 | 说明 |
|------|------|------|
| **策略模式** | `backend/` | AnthropicBackend/OpenAIBackend 实现同一接口 |
| **工厂函数** | `backend/__init__.py`、`runtime/events.py` | `create_backend()`、事件工厂 |
| **事件流** | `runtime/loop.py` | `AsyncIterator[RuntimeEvent]` 驱动 TUI/CLI/Server |
| **模板方法** | `runtime/compressor.py` | `run_pipeline()` 定义骨架，子方法定义步骤 |
| **注册表模式** | `capabilities/tools/registry.py` | `ToolRegistry` 管理工具集合 |

## 代码约定

- **文件头**：10-20 行模块文档字符串，说明职责 + 变更原因
- **导入顺序**：`__future__` → 标准库 → 第三方 → 项目内部（相对导入）
- **类型标注**：公开方法必须标注参数和返回类型
- **命名**：文件小写+下划线，类大驼峰，私有方法单下划线前缀
- **编码风格**：`ruff` 标准（行宽 120，双引号）
