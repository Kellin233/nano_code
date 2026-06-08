# Nano Code

`nanocode` 是一个从零实现的轻量级编程 Agent CLI。当前实现直接位于仓库 `src/` 目录，并通过打包配置安装为 `nanocode` Python 包；支持 Anthropic 与 OpenAI-compatible Chat Completions 后端，通过流式工具调用循环完成代码阅读、编辑、命令执行、MCP 工具调用、skills、长期记忆、hooks、会话恢复与沙箱化 shell 执行。

本文档按当前源码实现整理。

## 快速开始

### 环境要求

- Python 3.10+
- Python 依赖：`anthropic`、`openai`、`prompt_toolkit`、`rich`
- 可选：
  - Linux 默认 `workspace` 沙箱依赖 `bubblewrap` 命令。
  - `microsandbox-*` 沙箱依赖 `microsandbox` Python SDK，可通过 `pip install -e .[sandbox]` 安装。

### 安装与运行

```bash
cd /root/EvoCode/nanocode
python -m pip install -e .

# 安装后
nanocode "hello"

# 或使用 Python module 入口
python -m nanocode "hello"
```

不带 prompt 时进入交互式 REPL：

```bash
nanocode
```

## API 与模型配置

### Anthropic 后端

```bash
export ANTHROPIC_API_KEY=sk-ant-xxx
# 可选：Anthropic-compatible endpoint
export ANTHROPIC_BASE_URL=https://your-anthropic-compatible-endpoint
nanocode "解释 nanocode/agent/core.py 的职责"
```

### OpenAI-compatible 后端

```bash
export OPENAI_API_KEY=sk-xxx
export OPENAI_BASE_URL=https://your-openai-compatible-endpoint/v1
nanocode --model gpt-4o "总结项目结构"
```

也可以用 `--api-base` 显式指定 OpenAI-compatible endpoint：

```bash
OPENAI_API_KEY=sk-xxx nanocode --api-base https://your-openai-compatible-endpoint/v1 --model gpt-4o "hello"
```

解析顺序：

1. 同时存在 `OPENAI_API_KEY` 和 `OPENAI_BASE_URL`：使用 OpenAI-compatible 后端。
2. 存在 `ANTHROPIC_API_KEY`：使用 Anthropic 后端。
3. 只有 `OPENAI_API_KEY`：使用 OpenAI-compatible 后端。
4. 没有可用 API key：CLI 退出并提示配置方式。

默认模型是 `claude-opus-4-6`，可通过 `--model/-m` 或 `NANO_CODE_MODEL` 覆盖。`--thinking` 只对支持 thinking 的 Anthropic Claude 模型生效；`opus-4-6` / `sonnet-4-6` 使用 adaptive thinking。

## CLI 用法

```bash
nanocode [options] [prompt]
```

常用示例：

```bash
nanocode "修复 nanocode/tools/runtime.py 里的 bug 并运行相关测试"
nanocode --accept-edits "补充 memory 检索逻辑测试"
nanocode --max-cost 0.50 --max-turns 20 "分析当前项目架构"
nanocode --resume
```

### 参数

| 参数 | 说明 |
| --- | --- |
| `--yolo`, `-y` | 使用 `bypassPermissions` 模式，跳过常规确认；显式 deny 规则仍会生效。 |
| `--accept-edits` | 自动允许文件编辑工具；危险 shell、敏感路径等仍按策略处理。 |
| `--dont-ask` | 对需要确认的动作自动拒绝，适合 CI 或非交互环境。 |
| `--thinking` | 启用 Anthropic extended thinking。 |
| `--model`, `-m` | 指定模型；默认 `claude-opus-4-6` 或 `NANO_CODE_MODEL`。 |
| `--api-base URL` | 指定 OpenAI-compatible API base URL。 |
| `--resume` | 恢复最近一次保存的会话。 |
| `--max-cost USD` | 达到估算费用上限后停止。 |
| `--max-turns N` | 达到 agentic turn 上限后停止。 |
| `--sandbox PROFILE` | 选择 shell 沙箱 profile。 |
| `--sandbox-network none\|default` | 配置沙箱网络模式。 |
| `--sandbox-image IMG` | microsandbox 使用的 OCI 镜像，默认 `python:3.12`。 |
| `--sandbox-memory MiB` | microsandbox 内存，默认 2048。 |
| `--sandbox-cpus N` | microsandbox CPU 数，默认 2。 |
| `--sandbox-readonly-workspace` | 将工作区只读挂载到沙箱。 |
| `--sandbox-no-network` | 禁用沙箱网络。 |
| `--sandbox-env NAME` | 将指定环境变量透传进沙箱，可重复传入。 |
| `--sandbox-extra-write PATH` | 允许沙箱额外写入某个主机路径，可重复传入。 |
| `--sandbox-allow-local-fallback` | 沙箱后端不可用时显式允许 fallback 到 local。 |
| `--help`, `-h` | 显示帮助。 |

## 交互式 REPL

不传 prompt 会启动交互式界面。输入系统优先使用 `prompt_toolkit`，在非 TTY、CI 或不可用环境下会退回普通 `input()`。

支持能力：

- transcript 风格输出对话、工具调用、工具结果、状态和 token 成本。
- prompt_toolkit 模式下支持输入历史、补全、动态增高的输入区、工作时 sticky footer。
- slash command 与 user-invocable skill 补全。
- `/multiline` 多行输入模式。
- `{tag` 到 `tag}` 的块输入 fallback。
- `/editor` 通过 `$EDITOR` 或 `$VISUAL` 打开外部编辑器。
- `Ctrl+C` 中断当前 Agent；空闲时连续两次 `Ctrl+C` 退出。

本地 REPL 命令不会进入模型上下文。未命中的 `/xxx` 会先尝试作为 user-invocable skill 调用；仍未命中时才作为普通用户输入交给模型。

| 命令 | 说明 |
| --- | --- |
| `/help` | 列出当前可用命令。 |
| `/clear` | 清空会话消息、token 统计、active skills 与启动上下文注入状态。 |
| `/cost`, `/tokens` | 显示 token、估算成本和 turn 预算。 |
| `/compact` | 手动压缩当前会话上下文。 |
| `/memory` | 列出当前项目长期记忆。 |
| `/skills` | 列出 user-invocable skills。 |
| `/model` | 显示当前模型。 |
| `/editor [draft]` | 打开外部编辑器编写 prompt。 |
| `/multiline` | 切换多行输入模式。 |
| `/exit`, `/quit` | 退出 REPL。 |
| `/<skill-name> [args]` | 调用某个 user-invocable skill。 |
| `exit`, `quit` | 兼容退出命令。 |

## 内置工具

模型可调用的内置工具来自 `tools/definitions.py`：

| 工具 | 说明 |
| --- | --- |
| `read_file` | 读取文件并返回带行号内容。 |
| `write_file` | 写入文件；不存在则创建，存在则覆盖。 |
| `edit_file` | 使用唯一 `old_string` 精确替换文本，返回简易 diff。 |
| `list_files` | 按 glob 列出文件，跳过 `.git`、虚拟环境和 `__pycache__`。 |
| `grep_search` | 正则搜索文件内容，返回路径、行号和匹配行。 |
| `run_shell` | 执行 shell 命令，受权限策略和沙箱配置控制。 |
| `skill` | 调用已注册 skill 并返回渲染后的 prompt。 |
| `web_fetch` | 抓取 URL 文本；HTML 会剥离标签。 |
| `agent` | 启动隔离上下文的子 Agent，类型为 `explore`、`plan` 或 `general`。 |
| `tool_search` | 按名称或关键词查找延迟工具定义。 |
| `list_mcp_resources` | 列出已连接 MCP server 暴露的 resources。 |
| `read_mcp_resource` | 读取指定 MCP resource。 |

执行细节：

- `read_file`、`list_files`、`grep_search`、`web_fetch`、MCP resource 读取属于只读工具，可并发执行。
- 修改已有文件前必须先 `read_file`；若读取后文件被外部修改，需要重新读取。
- 大工具结果会保存到 `~/.nanocode/tool-results/`，上下文中只保留预览和路径。
- 单个工具结果会截断到约 50,000 字符。

## 权限策略

权限模式由 CLI 参数映射到内部 `PermissionMode`：

| CLI 参数 | 内部模式 | 行为 |
| --- | --- | --- |
| 默认 | `default` | 读工具自动允许；新文件写入、危险 shell、受保护路径、工作区外路径可能要求确认。 |
| `--accept-edits` | `acceptEdits` | 自动允许编辑工具，但危险 shell 和敏感路径仍按策略处理。 |
| `--yolo` / `-y` | `bypassPermissions` | 跳过常规确认；路径策略和 deny 规则仍优先执行。 |
| `--dont-ask` | `dontAsk` | 需要确认的动作自动拒绝。 |

检查顺序：

1. `permissions/workspace.py` 检查受保护路径和工作区边界。
2. `permissions/rules.py` 读取 `~/.claude/settings.json` 与当前目录 `.claude/settings.json` 中的 `permissions.allow` / `permissions.deny`。
3. `permissions/policy.py` 根据模式、工具类型和 shell 风险返回 `allow`、`deny` 或 `confirm`。
4. `ToolRuntime` 执行确认、缓存本会话已确认项，并运行 hooks。

权限规则示例：

```json
{
  "permissions": {
    "allow": [
      "read_file",
      "grep_search",
      "run_shell(python -m unittest*)"
    ],
    "deny": [
      "run_shell(rm*)",
      "write_file(.env*)"
    ]
  }
}
```

规则格式为 `tool_name` 或 `tool_name(pattern)`。`run_shell` 匹配命令；文件工具匹配 `file_path`。MCP 工具可使用 `mcp__server` 前缀匹配同一 server 下的工具。

## Shell 沙箱

沙箱只作用于 `run_shell`。文件读写、grep、MCP、memory 和 skill 仍在主进程内运行。

| Profile | Backend | 工作区模式 | 默认网络 | 说明 |
| --- | --- | --- | --- | --- |
| `workspace` | `bwrap` | 工作区可写 | `none` | Linux 默认，适合日常开发任务。 |
| `read-only` | `bwrap` | 工作区只读 | `none` | 只允许命令读取项目。 |
| `local` | `local` | 主机全访问 | `default` | 非 Linux 默认，或显式主机执行。 |
| `danger-full-access` | `local` | 主机全访问 | `default` | 语义上等同本地全访问。 |
| `microsandbox` | `microsandbox` | 根据 `--sandbox-readonly-workspace` 映射为 dev/safe | `none` | 便捷别名。 |
| `microsandbox-dev` | `microsandbox` | 工作区可写 | `none` | microVM 开发/测试。 |
| `microsandbox-safe` | `microsandbox` | 工作区只读 | `none` | microVM 检查不可信项目。 |
| `microsandbox-strict` | `microsandbox` | 工作区只读 | `none` | 禁止 fallback 到 local。 |

示例：

```bash
nanocode --sandbox workspace "运行测试"
nanocode --sandbox read-only "分析项目结构"
nanocode --sandbox local "在主机环境运行命令"
nanocode --sandbox microsandbox-safe --sandbox-image python:3.12 "检查不可信项目"
nanocode --sandbox workspace --sandbox-network default "运行需要网络的测试"
nanocode --sandbox workspace --sandbox-env HTTP_PROXY --sandbox-extra-write /tmp/build-cache "构建项目"
```

对应环境变量：

- `NANO_CODE_SANDBOX`
- `NANO_CODE_SANDBOX_IMAGE`
- `NANO_CODE_SANDBOX_MEMORY`
- `NANO_CODE_SANDBOX_CPUS`
- `NANO_CODE_SANDBOX_NETWORK`
- `NANO_CODE_SANDBOX_ENV`：逗号分隔的环境变量名。
- `NANO_CODE_SANDBOX_ALLOW_LOCAL_FALLBACK=1`

如果后端不可用，`run_shell` 返回错误。只有传入 `--sandbox-allow-local-fallback` 或设置 `NANO_CODE_SANDBOX_ALLOW_LOCAL_FALLBACK=1` 时，非 strict profile 才会 fallback 到 local。

## MCP

MCP 配置由 `mcp/config.py` 合并以下文件：

1. `~/.claude.json`
2. `~/.claude/settings.json`
3. 当前目录 `.claude/settings.json`
4. 当前目录 `.mcp.json`

支持 `mcpServers` 格式；若文件根对象本身就是 server 映射，也会尝试解析。

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "${PWD:-.}"],
      "env": {
        "EXAMPLE_TOKEN": "${EXAMPLE_TOKEN:-}"
      },
      "timeout": 15,
      "callTimeout": 60,
      "alwaysLoad": true
    }
  }
}
```

当前连接实现使用 stdio transport。配置中会解析：`command`、`args`、`env`、`url`、`transport`、`timeout`、`callTimeout` / `call_timeout`、`alwaysLoad` / `always_load`。`${VAR}` 与 `${VAR:-default}` 会按环境变量展开，未设置且无默认值时产生诊断信息。

MCP 工具注册时会生成安全的前缀名称，避免和内置工具冲突。工具变更通知会更新工具注册表，并向上下文注入变更摘要。

MCP 工具输出支持文本、JSON、resource 与 blob。较大的内容会落盘到 `~/.nanocode/mcp-output/`（由 `mcp/output.py` 管理），再把路径返回给模型。

## Skills

Skills 从以下目录发现：

- 用户级：`~/.claude/skills/<name>/SKILL.md`
- 项目级：`./.claude/skills/<name>/SKILL.md`

项目级同名 skill 会覆盖用户级。发现阶段只读取 `SKILL.md` frontmatter，正文在调用时懒加载。

示例：

```markdown
---
name: commit
description: Prepare a concise git commit
when_to_use: When the user asks to commit changes
user_invocable: true
disable_model_invocation: false
context: inline
allowed_tools: read_file, grep_search, run_shell
argument_hint: "message"
---

Review the current diff and prepare a commit.

User arguments: $ARGUMENTS
Skill directory: ${CLAUDE_SKILL_DIR}
```

支持的 metadata：

| 字段 | 说明 |
| --- | --- |
| `name` | skill 名称；默认目录名。 |
| `description` | 简短描述。 |
| `when_to_use` / `when-to-use` | 模型何时使用。 |
| `allowed_tools` / `allowed-tools` | 允许工具列表，支持 JSON 数组或逗号分隔。 |
| `disallowed_tools` / `disallowed-tools` | 禁用工具列表。 |
| `user_invocable` / `user-invocable` | 是否可由 `/skill-name` 调用，默认 true。 |
| `disable_model_invocation` / `disable-model-invocation` | 是否禁止模型通过 `skill` 工具调用，默认 false。 |
| `context` | `inline` 或 `fork`，默认 `inline`。 |
| `agent` | 可指定子 Agent 类型或名称。 |
| `argument_hint` / `argument-hint` | 参数提示。 |

正文占位符：

- `$ARGUMENTS` / `${ARGUMENTS}`：完整参数字符串。
- `$0`、`$1` 或 `$ARGUMENTS[0]`：shell 风格拆分后的第 N 个参数。
- `${CLAUDE_SKILL_DIR}`：skill 所在目录。

如果调用时传入参数，但正文未使用任何参数占位符，参数会追加到正文末尾的 `ARGUMENTS:` 区块。

## 子 Agent

`agent` 工具可启动隔离上下文的子 Agent：

- `explore`：只读探索。
- `plan`：只读规划，适合拆解任务。
- `general`：完整工具能力。

子 Agent 复用主 Agent 的模型和运行时能力，但有独立消息上下文；执行结果作为父 Agent 的工具结果返回。

## 长期记忆

长期记忆按项目路径 hash 存储在：

```text
~/.nanocode/projects/<project-hash>/memory/
```

每条记忆是一个带 frontmatter 的 Markdown 文件，`MEMORY.md` 是索引文件。`/memory` 会列出当前项目 active 记忆。

记忆条目支持类型和状态校验，常见字段包括：

- `memory_id`
- `name`
- `description`
- `type`
- `status`
- `keywords` / `entities` / `topics`
- `importance` / `confidence`
- `created_at` / `updated_at` / `last_accessed_at`
- `superseded_by`

当工具写入 memory 目录下的 `.md` 记忆文件时，会自动同步 `MEMORY.md` 索引。

## Hooks

Hooks 从 `~/.claude/settings.json` 加载。项目级 `.claude/settings.json` 默认不加载，只有设置 `NANO_CODE_TRUST_PROJECT_HOOKS=1` 后才会加载。

支持事件：

- `UserPromptSubmit`
- `PreToolUse`
- `PostToolUse`
- `Stop`

配置示例：

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "run_shell",
        "command": "python .claude/hooks/check_shell.py",
        "timeout_ms": 3000,
        "fail_closed": true
      }
    ],
    "PostToolUse": [
      {
        "matcher": "*",
        "command": "python .claude/hooks/audit.py"
      }
    ]
  }
}
```

Hook 输入通过 JSON 传给命令，包含：`event`、`session_id`、`cwd`、`prompt`、`tool_name`、`tool_input`、`tool_result`、`last_assistant_text`。

Hook 输出可返回：

- `allow`：允许。
- `deny`：拒绝，通常带 `reason`。
- `modify`：修改工具输入，使用 `updated_input`。
- `append_context`：在 `PostToolUse` 后追加上下文，使用 `content`。

`matcher` 为 `*` 或具体工具名；工具事件会按 `tool_name` 匹配。

## 会话与上下文

- 会话自动保存到 `~/.nanocode/sessions/<session-id>.json`。
- `--resume` 会读取最新会话并恢复 Anthropic 或 OpenAI 消息历史。
- 启动时会构建系统提示词与 startup context，包括项目、git、CLAUDE/AGENTS 文档等上下文附件。
- `compact` 可手动触发上下文压缩；Agent 也会根据上下文窗口和空闲状态做压缩/裁剪。
- token 成本按当前实现估算：输入 `$3 / 1M tokens`，输出 `$15 / 1M tokens`。

## 源码结构

```text
nanocode/
  __main__.py              # CLI 参数解析、后端选择、REPL/一次性任务入口
  agent/
    core.py                # Agent 状态容器和公开入口
    engine.py              # SessionEngine 事件驱动执行入口
    loop.py                # 主 agentic loop
    backends.py            # Anthropic/OpenAI streaming 适配
    context.py             # 消息上下文、记忆、压缩
    tools_runtime.py       # skill、sub-agent、MCP 等工具路由
  tools/                   # 内置工具定义、注册、运行管线
  permissions/             # 权限规则、shell 风险、工作区策略
  sandbox/                 # local / bubblewrap / microsandbox 后端
  mcp/                     # MCP 配置、连接、工具与 resource 输出
  skill/                   # skill 发现、调用、active skill 管理
  memory/                  # 项目级长期记忆存储与索引
  hooks/                   # hook 配置、输入输出协议、命令执行
  tui/                     # 交互式 transcript UI 与 slash commands
  context/                 # startup context、git/CLAUDE 文档附件
```

## 当前限制

- Linux 默认沙箱是 `workspace`，如果系统未安装 `bubblewrap`，`run_shell` 会失败；可显式使用 `--sandbox local` 或安装 bubblewrap。
- MCP 配置解析接受 http/sse/ws 配置字段，但当前可用连接实现主要是 stdio。
- OpenAI-compatible 后端使用 function calling 格式；不同服务商对 streaming/tool_calls/usage 的兼容性可能不同。
