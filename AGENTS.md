# Nano Code Agent 指南

请使用和用户相同的语言回复。

## 作用范围

- 本指南适用于 `nano_code` 仓库。主要实现位于 `nano_code/` Python 包下。
- 以代码作为事实来源。`docs/` 和 `remake/` 是有用的设计记录，但其中部分文本仍包含 `mini_claude` 等旧名称，或历史上的全局 Plan Mode 行为。
- 不要修改 `__pycache__/` 等生成缓存。
- 工作区可能已有用户的未提交改动。保持改动聚焦，绝不要回滚无关修改。

## 常用命令

在仓库根目录运行：

```bash
python -m pip install -e .
python -m nano_code "hello"
python -m compileall nano_code
python -m unittest discover -s test -v
python -m unittest discover -s test/v1 -v
```

CLI 入口也会安装为 `nano-code`。

只有任务明确需要时才进行真实 API 调用。代码支持通过 `ANTHROPIC_API_KEY` 使用 Anthropic，也支持通过 `OPENAI_API_KEY` 加 `OPENAI_BASE_URL` 使用 OpenAI 兼容接口。

## 项目地图

- `nano_code/__main__.py`：CLI 参数解析、one-shot 模式、REPL 命令、API key 选择、sandbox 参数、会话恢复。
- `nano_code/agent/core.py`：稳定的 `Agent` 门面和共享运行时状态。必须保持 `from nano_code.agent import Agent` 可用。
- `nano_code/agent/engine.py`：单次用户提交的编排、MCP 初始化、`UserPromptSubmit` hook 和自动保存。
- `nano_code/agent/loop.py`：面向 Anthropic 和 OpenAI 兼容接口的事件驱动主循环。负责保持工具调用和工具结果配对，并产出 UI 事件。
- `nano_code/agent/backends.py` 和 `nano_code/agent/models.py`：模型协议适配、流式解析、重试、模型能力和 token 逻辑。
- `nano_code/agent/context.py`：消息历史、上下文压力处理、记忆预取、压缩和 compact。
- `nano_code/agent/tools_runtime.py`：需要访问 Agent 状态的辅助路径，包括 `agent`、`skill`、`tool_search`、MCP 路由和兼容入口。
- `nano_code/tools/`：工具契约、schema、注册表、内置实现和 `ToolRuntime`。
- `nano_code/permissions/`：权限模式、allow/deny 规则、protected path、workspace 边界和 shell 风险检查。
- `nano_code/hooks/`：`UserPromptSubmit`、`PreToolUse`、`PostToolUse`、`Stop` hook 的加载和执行。
- `nano_code/sandbox/`：本地 shell 和可选 microsandbox shell 执行后端。
- `nano_code/memory/`：记忆存储、召回、渲染和压缩。
- `nano_code/skill/`：技能发现、active skill 状态、调用和提示词渲染。
- `nano_code/subagent.py`：内置 `explore`、`plan`、`general` 子 agent 配置，以及项目级/用户级自定义 agent。
- `nano_code/mcp_client.py`：MCP subprocess JSON-RPC 客户端，以及 `mcp__server__tool` 工具转换。
- `nano_code/prompt.py`：系统提示词模板、`CLAUDE.md` include、`.claude/rules/*.md`、memory/skill/agent/deferred-tool 注入。
- `nano_code/session.py`：会话持久化和恢复辅助函数。
- `test/`：单元测试。`test/v1/` 包含更完整的重构和回归覆盖。

## 架构规则

- 保持当前事件流架构。`Agent.chat()` 消费 `SessionEngine.submit()`，后者运行 `AgentLoop`，`AgentLoop` 调用模型后端，并把工具执行交给 `ToolRuntime`。
- 除非用户明确要求，不要恢复旧的全局 Plan Mode。当前规划能力是 `agent` 工具中 type 为 `plan` 的只读子 agent。
- 普通工具应保持无状态。普通工具 schema 放在 `tools/definitions.py`，实现放在 `tools/builtin.py` 或 `Tool` adapter 中，执行元数据通过 `ToolRegistry` 管理。
- 需要访问 Agent 状态的工具逻辑放在 `agent/tools_runtime.py` 或相关 Agent mixin 路径中，例如子 agent、skill、MCP、token 统计、输出 buffer 和确认提示。
- 工具错误通常应返回 `ToolResult(..., is_error=True)`，或返回会被转换为工具结果的 `"Error: ..."` 字符串。不要让普通工具失败直接打断主循环。
- 保持“先读后改”不变量。已有文件在 `write_file` 或 `edit_file` 前必须先 `read_file`，读取后 mtime 变旧时必须要求重新读取。
- 保持权限检查顺序：protected path/workspace 检查和 deny 规则不能被 `bypassPermissions` 绕过。
- 默认不信任项目级 hooks。`HookManager.capture()` 只有在 `NANO_CODE_TRUST_PROJECT_HOOKS=1` 时才加载项目 `.claude/settings.json` hooks。
- 只有真正只读或可安全并行的工具才能标记为 concurrency-safe。当前内置并发工具是 `read_file`、`list_files`、`grep_search` 和 `web_fetch`。
- 保持 provider 消息不变量。Anthropic 的 `tool_use` block 必须收到匹配的 `tool_result` block；OpenAI tool call 必须收到匹配的 `role: tool` 消息。
- 只在安全的对话边界执行 compact。避免在未完成的 tool-call/result 交换中间改变压缩逻辑。
- `Stop` hooks 可以通过追加 user context 强制主循环再跑一轮。修改退出逻辑时必须保留这个行为。
- 只读子 agent 类型（`explore`、`plan`）只能拿到 `read_file`、`list_files` 和 `grep_search`。`general` 和自定义子 agent 不应拿到 `agent` 工具，以避免递归创建。
- MCP 工具通过 `ToolRegistry.add_many(..., origin="mcp")` 注册，并按 `mcp__server__tool` 名称路由。不要在模型主循环里为 MCP 写特殊分支。
- Sandbox 是可选能力。即使没有安装 `microsandbox` extra，也必须保持本地执行可用。

## 测试要求

- 修改源码后，先运行最聚焦的相关 unittest 模块；如果改动跨模块，再运行更广的 discover。
- 修改架构或运行时逻辑时，至少优先运行：

```bash
python -m compileall nano_code
python -m unittest discover -s test -v
```

- 如果重构涉及 tool runtime、permissions、hooks、sandbox、MCP、session、prompt、memory、skills 或 event loop，还要运行：

```bash
python -m unittest discover -s test/v1 -v
```

- 除非用户明确要求集成测试覆盖，否则单元测试不要依赖真实 Anthropic/OpenAI API、真实 MCP subprocess 或真实 microsandbox 容器。

## 编码风格

- Python 目标版本是 `>=3.11`；保持依赖最小化（`anthropic`、`openai`、`rich`，以及可选的 `microsandbox`）。
- 遵循现有轻量模式：`asyncio`、`dataclasses`、`pathlib`、小模块和 `unittest`。
- 保持 `nano_code/tools/__init__.py` 和 `nano_code/agent/__init__.py` 中的公开导出稳定。
- 优先小范围改动，而不是大范围重写。没有明确理由时，不要跨模块移动职责。
- 现有代码和文档经常使用中文注释和解释文字；在附近新增注释或文档时可以匹配这种风格。
- 如果文档改动描述运行时行为，先核对当前代码，并明确标注历史行为或已过时行为。
