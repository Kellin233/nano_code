# Nano Code

`nanocode` 是一个轻量级编程 Agent CLI。它把模型调用、工具执行、权限确认、上下文压缩、TUI 和 headless server 拆成清晰的分层结构，代码位于 `src/`，安装后提供 `nanocode` 命令。

当前架构的核心约束是：Agent core 只管状态机和协议，不持有具体能力；`AgentSession` 是唯一装配点；能力模块全部在应用层组合。

## 快速开始

### 环境要求

- Python 3.10+
- 依赖：`anthropic`、`openai`、`prompt_toolkit`、`rich`
- 可选：Linux 默认 sandbox 依赖 `bubblewrap`；microsandbox 依赖 `microsandbox` SDK

### 安装与运行

```bash
cd /path/to/nanocode
pip install -e .

nanocode "hello"                 # 一次性执行
nanocode                         # 交互式 REPL
nanocode --server stdio          # JSONL server 模式
```

## API 配置

```bash
# Anthropic
export ANTHROPIC_API_KEY=sk-ant-xxx
nanocode "解释项目架构"

# OpenAI-compatible
export OPENAI_API_KEY=sk-xxx
export OPENAI_BASE_URL=https://your-endpoint/v1
nanocode --model gpt-4o "hello"
```

默认模型是 `claude-opus-4-6`，可通过 `--model/-m` 或 `NANO_CODE_MODEL` 覆盖。

## CLI 参数

| 参数 | 说明 |
|------|------|
| `--yolo`, `-y` | 跳过普通确认；deny 规则、workspace 外写入和受保护路径仍生效 |
| `--accept-edits` | 自动允许文件编辑，仍确认危险 shell |
| `--dont-ask` | 自动拒绝确认，适合 CI |
| `--thinking` | 启用 Anthropic extended thinking |
| `--model`, `-m` | 指定模型 |
| `--api-base` | OpenAI-compatible API 地址 |
| `--resume` | 恢复最近会话 |
| `--max-cost` | 最大花费，单位美元 |
| `--max-turns` | 最大 agentic turn 数 |
| `--allowed-tools` | 本次运行的工具白名单 |
| `--sandbox PROFILE` | Shell 沙箱 profile |
| `--help`, `-h` | 显示帮助 |

## REPL 命令

| 命令 | 说明 |
|------|------|
| `/help` | 列出命令 |
| `/clear` | 清空会话 |
| `/cost`, `/tokens` | 显示 token 用量和费用 |
| `/compact` | 手动压缩上下文 |
| `/memory` | 显示本地记忆摘要，支持 `path` 和 `show <topic>` |
| `/remember` | 显式写入本地记忆：`/remember <topic> <text>` |
| `/skills` | 列出可用 skills |
| `/model` | 显示当前模型 |
| `/editor` | 打开外部编辑器 |
| `/multiline` | 切换多行输入 |
| `/exit`, `/quit` | 退出 |

## 内置工具

| 工具 | 说明 |
|------|------|
| `read_file` | 读取文件，返回带行号内容 |
| `write_file` | 写入文件 |
| `edit_file` | 精确字符串替换编辑 |
| `list_files` | glob 匹配文件列表 |
| `grep_search` | 正则搜索文件内容 |
| `run_shell` | 通过 sandbox 执行 shell 命令 |
| `skill` | 调用已注册 skill |
| `web_fetch` | 抓取 URL 文本 |
| `agent` | 启动子 Agent |
| `tool_search` | 搜索并激活 deferred 工具 |
| `list_mcp_resources` | 列出 MCP resources |
| `read_mcp_resource` | 读取 MCP resource |

## Shell 沙箱

| Profile | Backend | 说明 |
|---------|---------|------|
| `workspace` | bwrap | Linux 默认，workspace 可写，网络关闭 |
| `read-only` | bwrap | workspace 只读，适合审查 |
| `local` | local | 主机全访问，调试用 |
| `danger-full-access` | local | 显式全访问 |
| `microsandbox-safe` | microsandbox | 只读 microVM |
| `microsandbox-dev` | microsandbox | 可写 microVM |
| `microsandbox-strict` | microsandbox | 最保守 microVM |

## 源码结构

```
src/
├── agent/                         # Agent core：状态机、协议、事件、核心类型
│   ├── agent.py                   # Agent 状态容器和回调槽位
│   ├── loop.py                    # LLM/tool 循环，能力通过回调注入
│   ├── events.py                  # RuntimeEvent 工厂函数
│   ├── types.py                   # ToolDef / ToolCall / ToolResult / RuntimeEvent
│   ├── models.py                  # 模型元数据、schema 转换、retry helper
│   ├── budget.py                  # 费用估算
│   └── harness/                   # 运行框架：压缩、上下文、hooks、权限、持久化
├── providers/                     # LLM Provider 层，只依赖 agent/types.py
│   ├── base.py
│   ├── anthropic.py
│   └── openai.py
├── cli/
│   ├── args.py                    # 参数解析
│   ├── config.py                  # RuntimeConfig，应用层运行配置
│   ├── main.py                    # CLI 入口
│   ├── session.py                 # AgentSession，唯一装配点
│   ├── thread.py                  # RuntimeThread，server/TUI 事件流包装
│   └── core/                      # 应用能力：tools/sandbox/skills/memory/mcp/subagents/server/extensions
└── tui/                           # 终端 UI
```

## 运行工件与评测

每次用户请求都会在当前 workspace 下生成一次 run：

```text
<workspace>/.nanocode/runs/<run_id>/
├── trace.jsonl
└── report.json
```

这些文件用于审计、复盘和本地 benchmark。普通 CLI、TUI、server 都走同一套 `AgentSession.run(prompt)`，不需要额外传 `--trace-out` 或 `--report-out`。

`report.json` 的 runtime 信息会记录本次运行的 `allowed_tools`。如果 CLI 或 benchmark task 设置了工具白名单，nanoCode 会过滤暴露给模型的 tool schema，并在工具执行边界拒绝越界 tool call。

## Project Instructions 与 Local Memory

项目共享规则来自：

```text
AGENTS.md
.nanocode/rules/*.md
```

本地记忆是用户私有的 markdown 上下文，存放在：

```text
~/.nanocode/projects/<repo_key>/memory/
├── MEMORY.md
├── preferences.md
├── project.md
└── debugging.md
```

`MEMORY.md` 只做索引；topic 文件按需创建。memory 只保存无法从当前代码、Git 或项目文档推导出的跨会话信息，例如用户偏好、行为反馈、项目决策、外部约束和稳定环境坑。源码结构、文件摘要、最近读过的文件、普通修复步骤不进入 memory。

### 本地 fixture benchmark

仓库内置了轻量级本地 fixture benchmark。当前任务集有 41 个任务；默认 `core` suite 有 34 个任务，另有 `security`、`permissions`、`memory`、`resume` 和 `all` suite。

```bash
# 只校验任务文件和生成空 benchmark artifact，不调用模型
python benchmarks/local-fixture/run.py \
  --dry-run \
  --limit 2 \
  --output-root /tmp/nanocode-local-fixture-results \
  --run-name smoke-dry-run

# 真实执行前需要配置模型 API key，会产生模型调用成本
export ANTHROPIC_API_KEY=sk-ant-xxx
python benchmarks/local-fixture/run.py \
  --limit 2 \
  --timeout 180 \
  --output-root benchmarks/local-fixture/results \
  --run-name smoke
```

跑全部任务时不要传 `--limit` 或 `--task-id`：

```bash
python benchmarks/local-fixture/run.py \
  --suite all \
  --timeout 180 \
  --output-root benchmarks/local-fixture/results \
  --run-name "full-$(date +%Y%m%d-%H%M%S)" \
  --stream
```

OpenAI-compatible endpoint 可按普通 CLI 方式配置：

```bash
export OPENAI_API_KEY=sk-xxx
export OPENAI_BASE_URL=https://your-endpoint/v1
python benchmarks/local-fixture/run.py --model gpt-4o --limit 2
```

常用参数：

| 参数 | 说明 |
|------|------|
| `--task-file` | 任务定义文件，默认 `benchmarks/local-fixture/tasks.json` |
| `--task-id` | 只运行指定任务，可重复传入 |
| `--suite` | 选择 `core`、`security`、`permissions`、`memory`、`resume` 或 `all` |
| `--limit` | 只取前 N 个任务 |
| `--timeout` | 每个任务的 nanocode/verifier 超时时间，默认 180 秒 |
| `--model` | 传给 nanocode 的模型覆盖值 |
| `--stream` | 任务运行时实时打印 nanocode 和 verifier 输出，同时仍保存 artifact |
| `--output-root` | benchmark 输出根目录 |
| `--run-name` | 本次 benchmark run 名称 |
| `--dry-run` | 只验证任务选择和写 `benchmark.json`，不执行 nanocode |

真实运行时 runner 会在每个任务开始和结束打印进度，例如 `[###---------------------] 3/41 passed=2 failed=1 finished python_clamp=pass`。`--stream` 只控制 nanocode/verifier 的实时 stdout/stderr，进度输出默认开启。

输出结构：

```text
<output-root>/<run-name>/
├── benchmark.json
├── tasks/<task-id>/
│   ├── task_result.json
│   ├── report.json
│   ├── trace.jsonl
│   ├── patch.diff
│   ├── nanocode_stdout.txt
│   ├── nanocode_stderr.txt
│   └── verifier_output.txt
└── workspaces/<task-id>/<fixture_repo>/
```

判定逻辑同时检查：

- nanocode 进程退出码为 0
- verifier 通过
- 预期 artifact 存在
- run report 存在
- `tool_steps <= step_budget`
- `stop_reason == "stop"`
- 实际使用工具未超出 task 的 `allowed_tools`

`--dry-run` 不执行任务，所以 `benchmark.json` 会记录选中的 task id，但 `rows` 为空、汇总为 `0/0`。
summary 还会记录 `selected_tasks`、`executed_tasks`、类别通过率、耗时统计和按类别平均工具步数。

## 架构原则

- `agent/` 不 import `cli/`、`tui/`、`providers/`、SDK 或具体能力模块。
- `agent/harness/` 可以做 I/O，但不依赖 `cli/`、`tui/`、`providers/`。
- `providers/` 只封装模型厂商差异，不接触 AgentSession 或 TUI。
- `cli/core/` 是能力层，包含 tools、sandbox、skills、memory、MCP、subagents、server/protocol、extensions。
- `cli/config.py` 持有应用层 `RuntimeConfig`，并转换为 Agent core 的 `AgentConfig`。
- `cli/session.py` 负责把 Agent、Backend、ToolRuntime、MemoryRuntime、HookManager、ExtensionRunner、persistence 等装配起来。

## 文档

完整设计文档见 `docs/` 目录，包含架构总览、各子系统详解、测试指南和代码导读。
