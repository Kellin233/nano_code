# Nano Code

`nanocode` 是一个从零实现的轻量级编程 Agent CLI。代码位于 `src/` 目录，通过打包配置安装为 `nanocode` Python 包；支持 Anthropic 与 OpenAI-compatible Chat Completions 后端，通过流式工具调用循环完成代码阅读、编辑、命令执行、MCP 工具调用、skills、长期记忆、hooks、会话恢复与沙箱化 shell 执行。

本文档按当前源码实现（2026-06-08）整理。

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
nanocode "解释 src/runtime/agent/core.py 的职责"
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

默认模型是 `claude-opus-4-6`（定义在 `runtime/agent/models.py` 的 `DEFAULT_MODEL`），可通过 `--model/-m` 或 `NANO_CODE_MODEL` 覆盖。`--thinking` 只对支持 thinking 的 Anthropic Claude 模型生效；`opus-4-6` / `sonnet-4-6` 使用 adaptive thinking。

## CLI 用法

```bash
nanocode [options] [prompt]
```

常用示例：

```bash
nanocode "修复 src/domains/tools/runtime.py 里的 bug 并运行相关测试"
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

模型可调用的内置工具来自 `domains/tools/definitions.py`：

| 工具 | 说明 |
| --- | --- |
| `read_file` | 读取文件并返回带行号内容。 |
| `write_file` | 写入文件；不存在则创建，存在则覆盖。自动同步 memory 索引。 |
| `edit_file` | 使用唯一 `old_string` 精确替换文本，返回简易 diff。支持智能引号规范化匹配。 |
| `list_files` | 按 glob 列出文件，跳过 `.git`、虚拟环境和 `__pycache__`。上限 200 条。 |
| `grep_search` | 正则搜索文件内容，优先使用系统 grep（参数列表，非 shell），fallback 到 Python 实现。 |
| `run_shell` | 执行 shell 命令，**必须在 sandbox manager 或 execution backend 内执行**，无沙箱时拒绝执行。 |
| `skill` | 调用已注册 skill 并返回渲染后的 prompt 或启动 fork 子 Agent。 |
| `web_fetch` | 抓取 URL 文本；HTML 会剥离标签，默认最大 50,000 字符。 |
| `agent` | 启动隔离上下文的子 Agent，类型为 `explore`、`plan` 或 `general`。继承父级权限模式。 |
| `tool_search` | 按名称或关键词查找延迟工具定义。 |
| `list_mcp_resources` | 列出已连接 MCP server 暴露的 resources。 |
| `read_mcp_resource` | 读取指定 MCP resource。 |

执行细节：

- `read_file`、`list_files`、`grep_search`、`web_fetch`、MCP resource 读取属于只读工具，可并发执行。
- 修改已有文件前必须先 `read_file`；若读取后文件被外部修改，需要重新读取。
- 大工具结果会落盘到 `~/.nanocode/tool-results/`（阈值 `LARGE_RESULT_BYTES = 30 KB`），上下文中只保留预览和路径。
- 单个工具结果字符串会截断到 `MAX_RESULT_CHARS = 50,000` 字符。

### run_shell 安全模型

`run_shell` 是唯一执行任意命令的工具，有**两层强制安全约束**：

1. **ToolRuntime 路径**（通过 `_call_builtin`）：要求 `ctx.sandbox_manager is not None`，否则返回错误。
2. **execute_builtin_tool 路径**：要求 `execution_backend is not None`，否则返回错误。

两个路径都**禁止回退到裸 `subprocess.run(..., shell=True)`**。`builtin.py` 中的 `run_shell` 函数保留作为实现参考，但 `BUILTIN_HANDLERS` 字典已不再引用它。

非 `run_shell` 工具（如 `grep_search`、git context、TUI editor）使用参数列表形式的 `subprocess.run`（非 `shell=True`），不受此限制。

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
2. `permissions/rules.py` 读取 `~/.claude/settings.json` 与当前目录 `.claude/settings.json` 中的 `permissions.allow` / `permissions.deny`。规则缓存可通过 `reset_permission_cache()` 清除。
3. `permissions/policy.py` 根据模式、工具类型和 shell 风险返回 `allow`、`deny` 或 `confirm`。
4. `permissions/shell.py` 使用 `DANGEROUS_PATTERNS` 和 `COMPLEX_SHELL_PATTERNS` 正则列表判断命令风险等级。
5. `ToolRuntime` 执行确认、缓存本会话已确认项，并运行 hooks。

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

规则格式为 `tool_name` 或 `tool_name(pattern)`。`run_shell` 匹配命令；文件工具匹配 `file_path`。MCP 工具可使用 `mcp__server` 前缀匹配同一 server 下的工具。deny 规则优先级高于 allow 规则，且即使在 `bypassPermissions` 模式下也会生效。

## 子 Agent 权限继承

`agent` 工具和 `fork` 模式的 skill 创建子 Agent 时，**子 Agent 继承父 Agent 的 `permission_mode`**，不再强制 `bypassPermissions`：

- 父级为 `default` → 子级也是 `default`（该确认的仍会确认）。
- 父级为 `bypassPermissions` → 子级也是 `bypassPermissions`。
- 父级为 `dontAsk` → 子级也是 `dontAsk`（不出现权限升级）。

如果需要在子 Agent 中跳过确认，应显式使用 `--yolo` 启动父 Agent。sandbox manager 实例在父子之间共享，保持隔离边界一致。

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

`SandboxBackend` 是一个 Protocol，`_build_backend()` 根据配置创建对应实例。各 backend 实现 `is_available()` / `start()` / `run_shell()` / `stop()` 接口。

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

沙箱后端不可用时返回错误。只有传入 `--sandbox-allow-local-fallback` 或设置 `NANO_CODE_SANDBOX_ALLOW_LOCAL_FALLBACK=1` 时，非 strict profile 才会 fallback 到 local。`microsandbox-strict` 在任何情况下都不 fallback。

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

当前连接实现使用 stdio transport（`McpConnection`）。配置中会解析：`command`、`args`、`env`、`url`、`transport`、`timeout`、`callTimeout` / `call_timeout`、`alwaysLoad` / `always_load`。`${VAR}` 与 `${VAR:-default}` 会按环境变量展开，未设置且无默认值时产生诊断信息。

MCP 工具注册时通过 `McpManager._make_prefixed_name()` 生成安全的 `mcp__server__tool` 前缀名称，避免和内置工具冲突。`notifications/tools/list_changed` 通知会触发 debounced refresh（延迟 `MCP_REFRESH_DEBOUNCE_S = 0.2s`），并更新工具注册表。

MCP 工具输出支持文本、JSON、resource 与 blob。较大的内容会落盘到 `~/.nanocode/mcp-output/`（由 `mcp/output.py` 管理），再把路径返回给模型。

## Skills

Skills 从以下目录发现（`skills/registry.py`）：

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

子 Agent 复用主 Agent 的模型和运行时能力，但有独立消息上下文；执行结果作为父 Agent 的工具结果返回。子 Agent 继承父级的 `permission_mode`（见上文权限继承章节），sandbox 实例与父级共享。

## 长期记忆

长期记忆按项目路径 hash 存储在：

```text
~/.nanocode/projects/<project-hash>/memory/
```

每条记忆是一个带 frontmatter 的 Markdown 文件，`MEMORY.md` 是索引文件。`/memory` 会列出当前项目 active 记忆。

记忆条目支持类型和状态校验（`memory/types.py`），常见字段包括：

- `memory_id`
- `name`
- `description`
- `type`：`user` / `feedback` / `project` / `reference`
- `status`：`active` / `archived` / `superseded`
- `keywords` / `entities` / `topics`
- `importance` / `confidence`（0.0–1.0）
- `access_count`
- `created_at` / `updated_at` / `last_accessed_at`
- `superseded_by`

当工具写入 memory 目录下的 `.md` 记忆文件时，会自动同步 `MEMORY.md` 索引。记忆预取（`memory/retrieval.py`）在用户回合开始时通过侧查询选择相关记忆注入上下文。

## Hooks

Hooks 从 `~/.claude/settings.json` 加载（`hooks/config.py`）。项目级 `.claude/settings.json` 默认不加载，只有设置 `NANO_CODE_TRUST_PROJECT_HOOKS=1` 后才会加载。

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

Hook 输入通过 JSON 传给命令（`hooks/types.py` 的 `HookInput`），包含：`event`、`session_id`、`cwd`、`prompt`、`tool_name`、`tool_input`、`tool_result`、`last_assistant_text`。

Hook 输出可返回：

- `allow`：允许。
- `deny`：拒绝，通常带 `reason`。
- `modify`：修改工具输入，使用 `updated_input`。
- `append_context`：在 `PostToolUse` 后追加上下文，使用 `content`。

**PreToolUse hook 安全约束**：每次 hook 返回 `modify` 后，`ToolRuntime` 会对修改后的输入重新调用 `tool.validate()`。若校验失败（如 hook 移除了必填字段），执行会被阻断并返回错误。修改后的输入仍然会进入权限策略检查。

`matcher` 为 `*` 或具体工具名；工具事件会按 `tool_name` 匹配。

## 会话与上下文

- 会话自动保存到 `~/.nanocode/sessions/<session-id>.json`。
- `--resume` 会读取最新会话并恢复 Anthropic 或 OpenAI 消息历史。
- 启动时会构建系统提示词与 startup context（`context/prompt.py`、`context/system_prompt.py`），包括项目 CLAUDE.md / AGENTS.md、git 状态、skills 列表、deferred tools 等上下文附件（`context/attachments.py`）。
- 上下文压缩采用**三层流水线**（`runtime/agent/context.py`）：
  1. **预算裁剪**：利用率 > 50% 时裁剪超长工具结果。
  2. **陈旧 snip**：利用率 > 60% 时对可复现工具的旧结果做占位符替换。
  3. **microcompact**：空闲超过 5 分钟时进一步清理旧结果。
- compact 通过 `SessionEngine` 的 `_ensure_mcp` 首次连接 MCP，由 `AgentLoop` 驱动主循环。compaction 过程受异常保护：API 调用失败时降级为保留当前历史继续对话，不会中断用户会话。
- token 成本按当前实现估算：输入 `$3 / 1M tokens`，输出 `$15 / 1M tokens`。

## 统一常量管理

运行时常量集中在 `src/domains/tools/constants.py`，按类别组织：

| 类别 | 常量 | 说明 |
|------|------|------|
| 工具裁剪 | `MAX_RESULT_CHARS`、`LARGE_RESULT_BYTES` | 结果截断与落盘阈值 |
| 上下文压缩 | `SNIP_THRESHOLD`、`MICROCOMPACT_IDLE_S`、`KEEP_RECENT_RESULTS` 等 | 三层压缩流水线参数 |
| Shell | `DEFAULT_SHELL_TIMEOUT_MS` | 默认命令超时 30s |
| API | `DEFAULT_MAX_TOKENS`、`MAX_RETRIES` | API 调用参数 |
| 文件操作 | `MAX_LIST_FILES_RESULTS`、`MAX_GREP_RESULTS` 等 | 工具结果上限 |

常量模块被 `runtime.py`、`registry.py`、`builtin.py`、`context.py`、`core.py`、`models.py`、`backends.py` 等模块导入，避免魔数散落。

## Server 模式

`NanoCodeServer`（`server/app_server.py`）提供多线程会话管理，支持 JSON-RPC 风格的协议消息（`protocol/messages.py`）：

- `thread.create` / `thread.resume` / `thread.submit`
- `thread.abort` / `thread.compact`
- `approval.resolve`
- `session.list`

传输层支持 Unix socket、WebSocket、stdio（`server/transports/`）。

## 源码结构

```text
nanocode/
  src/
    __main__.py                     # CLI 参数解析、后端选择、REPL/一次性任务入口
    runtime/
      thread.py                     # RuntimeThread 公开入口
      config.py                     # RuntimeConfig
      approvals.py                  # 确认决策管理
      capability.py                 # CapabilityManager 插件体系
      events.py                     # RuntimeEvent
      agent/
        core.py                     # Agent 状态容器和公开入口
        engine.py                   # SessionEngine 事件驱动执行入口
        loop.py                     # AgentLoop 主循环（Anthropic + OpenAI）
        backends.py                 # Anthropic/OpenAI streaming 适配
        context.py                  # 三层压缩流水线、记忆预取、上下文注入
        tools_runtime.py            # skill、sub-agent、MCP 等工具路由
        events.py                   # AgentEvent 类型定义
        models.py                   # 模型窗口、thinking 支持、重试、默认模型
    domains/
      tools/
        constants.py               # 统一常量管理
        definitions.py             # 内置工具 JSON schema 定义
        builtin.py                 # read_file / write_file / edit_file 等实现
        registry.py                # ToolRegistry：工具注册、延迟激活、并发安全
        runtime.py                 # ToolRuntime：执行管线、hook、权限、结果截断
        base.py                    # Tool / ToolCall / ToolContext / ToolResult 协议
        types.py                   # ToolDef / ToolMetadata / PermissionMode 类型
        permissions.py             # 兼容包装器（重新导出 permissions/ 内容）
      permissions/
        policy.py                  # check_permission 统一入口
        rules.py                   # settings.json 加载、规则匹配、缓存
        shell.py                   # DANGEROUS_PATTERNS / check_shell_safety
        workspace.py               # 受保护路径与工作区边界检查
      sandbox/
        manager.py                 # SandboxManager：会话级后端生命周期
        backend.py                 # SandboxBackend Protocol + LocalBackend
        bwrap_backend.py           # Bubblewrap backend（Linux 默认）
        microsandbox_backend.py    # microsandbox microVM backend
        config.py                  # SandboxConfig 构建
        types.py                   # CommandResult / SandboxConfig
      mcp/
        manager.py                 # McpManager：多 server 连接、工具注册、resource
        client.py                  # （兼容入口）
        config.py                  # MCP 配置加载与合并
        connection.py              # McpConnection：stdio transport
        transport.py               # 传输层抽象
        types.py                   # McpServerConfig / McpToolDef / McpToolDelta
        output.py                  # 工具输出处理（文本、JSON、blob）
        resources.py               # resource 列表渲染
      skills/
        registry.py                # SkillRegistry：发现、解析 metadata
        invocation.py              # SkillInvocation：参数替换、权限过滤
        active.py                  # ActiveSkillManager：上下文重挂
        types.py                   # SkillDefinition / SkillInvocationResult
        prompt.py                  # skill 描述渲染
      memory/
        store.py                   # 记忆文件 CRUD、MEMORY.md 索引
        retrieval.py               # 语义预取与候选过滤
        rendering.py               # 上下文注入格式化
        consolidation.py           # 软归档与去重
        types.py                   # MemoryEntry / 校验常量
      hooks/
        config.py                  # HookManager：配置加载、事件匹配、运行
        runner.py                  # Hook 进程执行
        types.py                   # HookCommand / HookInput / HookOutput
      context/
        claude_md.py               # CLAUDE.md / AGENTS.md 发现与聚合
        git_context.py             # git 状态快照
        frontmatter.py             # Markdown frontmatter 解析
        prompt.py                  # build_prompt_bundle / build_system_prompt
        system_prompt.py           # 系统提示词模板
        attachments.py             # skill 列表、deferred tools 等上下文附件
        startup.py                 # 启动上下文构建
        types.py                   # 上下文类型定义
      subagents/
        __init__.py                # 子 Agent 类型配置与发现
    providers/
      anthropic.py                 # AnthropicProvider（新架构）
      openai_chat.py              # OpenAIChatProvider（新架构）
      base.py                      # ProviderConfig
    core/
      ports.py                     # ModelProvider / ToolExecutor 协议
      messages.py                  # CoreToolCall / CoreToolResult 等消息类型
      turn.py                      # CoreTurn 循环
    tui/
      app.py                       # REPL 主循环
      renderer.py                  # Rich 渲染器
      input.py                     # prompt_toolkit / fallback 输入
      commands.py                  # slash command 注册
      state.py                     # UI 状态
      theme.py                     # 主题配置
    session/
      event_store.py               # SessionEventStore / ArtifactStore
      snapshots.py                 # 会话持久化
      artifacts.py                 # 大结果 artifact 落盘
    server/
      app_server.py                # NanoCodeServer：多线程会话
      transports/
        unix_socket.py             # Unix socket transport
        websocket.py               # WebSocket transport
        stdio.py                   # stdio transport
    sdk/
      client.py                    # SDK 客户端
      thread.py                    # SDK 线程
    protocol/
      messages.py                  # 协议消息定义
      dispatcher.py                # 消息分发
    capabilities/                  # 插件能力（hooks/mcp/memory/skills/subagents/tools provider）
  test/                            # 155 个主测试
    test_tools.py                  # 权限、注册表、运行时、编辑工具测试
    test_sandbox.py                # sandbox 后端测试
    test_hooks_runtime.py          # hook 运行时测试
    test_agent_loop.py             # Agent 循环测试
    test_agent_skills.py           # skill 集成测试
    test_skills.py                 # skill 发现与调用测试
    test_mcp_refactor.py           # MCP 连接与管理器测试
    test_memory_*.py               # 记忆存储/检索/渲染/压缩测试
    test_src_review_2026_06_08.py  # src 审查新增测试（16 个）
    tui/                           # TUI 渲染器/命令/输入测试
    v1/                            # 22 个 v1 集成测试
  review/
    hook.md                        # 旧 hook 审查材料
    src-review-2026-06-08.md       # 2026-06-08 src 审查报告
    update.md                      # 2026-06-08 优化方案
```

## 当前限制

- Linux 默认沙箱是 `workspace`（bwrap），如果系统未安装 `bubblewrap`，`run_shell` 会失败；可显式使用 `--sandbox local` 或 `--sandbox-allow-local-fallback`。
- MCP 配置解析接受 http/sse/ws 配置字段，但当前可用连接实现主要是 stdio transport。
- OpenAI-compatible 后端使用 function calling 格式；不同服务商对 streaming/tool_calls/usage 的兼容性可能不同。
- `providers/` 目录（`AnthropicProvider` / `OpenAIChatProvider`）和 `core/` 目录是新架构抽象，当前主循环仍使用 `runtime/agent/` 中的 `AgentBackendMixin`。两套体系并存，尚未完成统一。
- `capabilities/` 插件体系已有 provider 骨架，但尚未定义统一的 lifecycle 协议（initialize → contribute → shutdown）。
- 部分模块缺少独立单元测试：`providers/`、`core/`、`domains/sandbox/microsandbox_backend.py`、`domains/mcp/transport.py`。
