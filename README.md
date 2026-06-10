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
| `--yolo`, `-y` | 跳过确认提示，deny 规则和受保护路径仍生效 |
| `--accept-edits` | 自动允许文件编辑，仍确认危险 shell |
| `--dont-ask` | 自动拒绝确认，适合 CI |
| `--thinking` | 启用 Anthropic extended thinking |
| `--model`, `-m` | 指定模型 |
| `--api-base` | OpenAI-compatible API 地址 |
| `--resume` | 恢复最近会话 |
| `--max-cost` | 最大花费，单位美元 |
| `--max-turns` | 最大 agentic turn 数 |
| `--sandbox PROFILE` | Shell 沙箱 profile |
| `--help`, `-h` | 显示帮助 |

## REPL 命令

| 命令 | 说明 |
|------|------|
| `/help` | 列出命令 |
| `/clear` | 清空会话 |
| `/cost`, `/tokens` | 显示 token 用量和费用 |
| `/compact` | 手动压缩上下文 |
| `/memory` | 列出长期记忆 |
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
│   └── harness/                   # 运行框架：压缩、上下文、hooks、权限、会话
├── providers/                     # LLM Provider 层，只依赖 agent/types.py
│   ├── base.py
│   ├── anthropic.py
│   └── openai.py
├── cli/
│   ├── args.py                    # 参数解析
│   ├── main.py                    # CLI 入口
│   ├── session.py                 # AgentSession，唯一装配点
│   ├── thread.py                  # RuntimeThread，server/TUI 事件流包装
│   └── core/                      # 应用能力：tools/sandbox/skills/memory/mcp/subagents/server/extensions
└── tui/                           # 终端 UI
```

## 架构原则

- `agent/` 不 import `cli/`、`tui/`、`providers/`、SDK 或具体能力模块。
- `agent/harness/` 可以做 I/O，但不依赖 `cli/`、`tui/`、`providers/`。
- `providers/` 只封装模型厂商差异，不接触 AgentSession 或 TUI。
- `cli/core/` 是能力层，包含 tools、sandbox、skills、memory、MCP、subagents、server/protocol、extensions。
- `cli/session.py` 负责把 Agent、Backend、ToolRuntime、MemoryRuntime、HookManager、ExtensionRunner 等装配起来。

## 文档

完整设计文档见 `docs/` 目录，包含架构总览、各子系统详解、测试指南和代码导读。
