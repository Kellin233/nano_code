# Nano Code

`nanocode` 是一个从零实现的轻量级编程 Agent CLI。代码位于 `src/` 目录，通过打包配置安装为 `nanocode` Python 包。支持 Anthropic 与 OpenAI-compatible 后端，提供流式工具调用循环、TUI 交互模式、headless server 模式。

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
nanocode --server stdio          # Server 模式
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

默认模型 `claude-opus-4-6`，可通过 `--model/-m` 或 `NANO_CODE_MODEL` 覆盖。

## CLI 参数

| 参数 | 说明 |
|------|------|
| `--yolo`, `-y` | 跳过确认提示（deny 规则仍生效） |
| `--accept-edits` | 自动允许文件编辑 |
| `--dont-ask` | 自动拒绝确认（CI 用） |
| `--thinking` | 启用 Anthropic extended thinking |
| `--model`, `-m` | 指定模型 |
| `--api-base` | OpenAI-compatible API 地址 |
| `--resume` | 恢复最近会话 |
| `--max-cost` | 最大花费（美元） |
| `--max-turns` | 最大对话轮次 |
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
| `/editor` | 打开外部编辑器 |
| `/exit`, `/quit` | 退出 |

## 内置工具

| 工具 | 说明 |
|------|------|
| `read_file` | 读取文件，返回带行号内容 |
| `write_file` | 写入文件 |
| `edit_file` | 精确字符串替换编辑 |
| `list_files` | glob 匹配文件列表 |
| `grep_search` | 正则搜索文件内容 |
| `run_shell` | 执行 shell 命令（必须通过 sandbox） |
| `skill` | 调用已注册 skill |
| `web_fetch` | 抓取 URL 文本 |
| `agent` | 启动子 Agent（explore/plan/general） |
| `tool_search` | 搜索延迟加载工具 |

## Shell 沙箱

| Profile | Backend | 说明 |
|---------|---------|------|
| `workspace` | bwrap | Linux 默认，日常开发 |
| `read-only` | bwrap | 只读审查 |
| `local` | local | 主机全访问 |
| `microsandbox-safe` | microsandbox | 只读 microVM |
| `microsandbox-dev` | microsandbox | 可写 microVM |

## 源码结构

```
src/
├── cli/                 # CLI 入口：参数解析 + 依赖组装
├── tui/                 # 终端 UI：交互式 REPL
├── server/              # JSONL 协议 Server
├── runtime/             # ★ Agent Runtime 内核
│   ├── agent.py         #   Agent 状态容器
│   ├── loop.py          #   主对话循环（后端无关）
│   ├── compressor.py    #   三层上下文压缩
│   └── events.py        #   运行时事件
├── backend/             # 模型后端（策略模式）
│   ├── anthropic.py     #   Anthropic Messages API
│   └── openai.py        #   OpenAI Chat Completions
├── capabilities/        # 能力模块
│   ├── tools/           #   工具系统：注册 + 内置 + 执行管线
│   ├── mcp/             #   MCP 协议集成
│   ├── skills/          #   Skill 系统
│   ├── hooks/           #   Hook 生命周期
│   ├── memory/          #   长期记忆
│   ├── sandbox/         #   Shell 沙箱
│   ├── permissions/     #   权限策略
│   └── subagents/       #   子 Agent 编排
├── context/             # 上下文构建：系统提示词 + 启动上下文 + 附件
├── models.py            # 模型元数据
├── session/             # 会话持久化
└── protocol/            # JSONL 消息协议
```

## 文档

完整设计文档见 `docs/` 目录，包含架构总览、各子系统详解和面试考点。
