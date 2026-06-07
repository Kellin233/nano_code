# 引言：为什么从零造一个 Claude Code？

## 本章目标

> 本教程已按当前仓库整理为 Python-only 版本：本项目实现均位于 `nano_code/`，命令入口为 `nano-code` 或 `python -m nano_code`。涉及 Claude Code 上游源码时，只作为架构对照；真正要阅读和运行的代码都在本仓库的 Python 文件里。

理解项目定位、技术栈选择和整体架构，5 分钟内跑起来你自己的编程智能体。

这份教程不是 API 手册，也不是把源码逐行翻译一遍。更合适的读法是：先知道一次请求会经过哪些模块，再回到代码里看每个模块为什么只负责这一小块。读完本章后，你应该能打开 `nano_code/agent/` 不再害怕它的结构，因为你知道里面大致分成：初始化、会话入口、事件流循环、压缩、工具执行、子智能体、Anthropic 后端、OpenAI 兼容后端这几段。

## 为什么要从零造？

### AI 编程的三个阶段

AI 辅助编程大致经历了三个阶段：**代码补全**（Copilot）→ **聊天助手**（Cursor Chat）→ **自主 Agent**（Claude Code）。

前两个阶段的共同限制是：**模型不能执行操作**。它只能给建议，无法自己跑测试看结果。

Claude Code 是一个质的飞跃。你说"给这个项目加用户注册功能"，它会自己搜索路由定义、读取数据库模型、创建 handler 文件、注册路由、写测试、运行测试命令、看到失败、修复、再跑——循环十几次，直到通过为止。

这就是 **受控工具循环 Agent**：模型是决策者，代码只是执行环境。

### Agent-first 意味着什么

传统程序里，代码逻辑决定行为——`if/else` 都是程序员预先写好的。Agent 架构反过来：**模型决定下一步做什么**，代码只提供循环框架和工具。

这并不表示代码变得不重要。恰恰相反，Agent 程序里的代码负责划清边界：模型可以提出“我要读这个文件”“我要运行测试”“我要编辑这一段”，但真正能不能执行、怎么执行、执行结果怎么回到上下文，都由代码控制。你可以把模型看成驾驶员，把工具系统、权限系统、上下文管理看成车辆本身的方向盘、刹车和仪表盘。

在当前 Python 版里，这个边界主要体现在 `nano_code/agent/`、`nano_code/tools/`、`nano_code/permissions/` 和 `nano_code/hooks/`。`agent` 负责会话入口和事件流循环，`tools` 负责强 Tool 契约与执行适配，`permissions` 负责统一安全策略，`hooks` 负责把用户可配置的拦截点接入主循环。模型只能通过这些定义好的工具影响真实世界，不能直接越过代码去操作文件系统。

整个系统的核心是一个 `while (true)` 循环：

```
while (true) {
    调用模型 → 模型返回响应
    if (响应包含工具调用) → 执行工具 → 把结果喂回模型 → 继续循环
    if (响应只是文本) → 任务完成，退出循环
}
```

**只有当模型的响应不包含任何工具调用时，循环才会退出**——是模型，而不是代码逻辑，决定任务是否完成。

### 为什么不直接读源码

Claude Code 的开源快照有 50 万行代码：66+ 工具、React/Ink TUI、MCP 协议、OAuth 认证、多代理系统……直接读很容易迷失在边界情况和抽象层里。

我们的做法：**只保留最小必要组件**，用 Python 代码复现核心能力（记忆、技能、多智能体、权限规则、分级压缩、预算控制、hooks、只读 plan 子 agent），每一步都尽量落到当前仓库的真实代码上。它不像完整 Claude Code 那样覆盖所有边界场景，但主干足够完整：模型能看项目、能调用工具、能写文件、能被权限拦住、能压缩上下文，也能把复杂任务交给子智能体。

## 核心概念速览

**智能体循环**：思考—行动—观察的循环。模型收到请求后决定调用哪个工具，系统执行工具并把结果反馈给模型，模型继续思考，直到不再发出工具调用。

**工具系统**：工具是智能体和真实世界交互的桥梁。我们在系统提示词里描述每个工具的名字和参数，模型需要时返回结构化的工具调用请求，代码执行后把结果喂回去。

工具系统的关键不是“有几个函数”，而是“模型和代码之间有一份契约”。工具定义告诉模型参数格式，执行函数把参数变成真实操作，权限层决定操作是否允许。后面第 2 章会展开这个契约怎么写、怎么执行、怎么防止误操作。

**上下文工程**：模型的表现完全取决于它看到了什么。上下文窗口有限（200K tokens），但复杂任务可能跑几十轮——所以需要压缩。我们实现了 4 级压缩：裁剪大块输出 → 摘要工具结果 → 模型总结整段对话，每级比上一级激进，系统尽量用最轻的方式解决问题。

这里的“上下文”包括系统提示词、用户消息、助手回复、工具调用、工具结果、项目规则、记忆和技能说明。很多 Agent 问题表面上看是“模型不聪明”，实际是上下文没给对：它没看到文件、没看到项目规则、看到了过期工具结果，或者上下文里塞了太多无关内容。第 7 章会讲怎么裁剪和摘要，第 8 章会讲长期记忆为什么不能简单等同于会话历史。

**系统提示词**：每次 API 调用前组装的第一条消息，告诉模型当前操作系统、工作目录、Git 状态、项目规则（CLAUDE.md）、可用工具列表。这些上下文直接影响模型的决策质量。

系统提示词不是静态说明书。当前项目的 `build_system_prompt()` 会动态收集当前目录、日期、平台、Git 状态、`CLAUDE.md`、`.claude/rules/*.md`、记忆、技能和子智能体描述。也就是说，同一个模型在不同项目目录下会看到不同工作规则，这是它能“适应项目”的基础。

**权限与安全**：能执行任意 Shell 命令的智能体需要安全控制。我们实现了 5 种权限模式，从"全部放行"到"全部询问用户"——写文件前检查是否允许，危险操作需要确认。

## 架构全景

```mermaid
graph TB
    User[用户输入] --> CLI[__main__.py<br/>CLI 入口 / REPL]
    CLI --> Core[agent/core.py<br/>Agent 门面 + UI 渲染]
    Core --> Engine[agent/engine.py<br/>会话入口]
    Engine --> Hooks[hooks/<br/>UserPromptSubmit / Stop]
    Engine --> Loop[agent/loop.py<br/>事件流主循环]
    Loop --> Prompt[prompt.py<br/>系统提示词]
    Loop --> API{API 后端}
    API -->|Anthropic| AnthropicSDK[Anthropic SDK]
    API -->|OpenAI 兼容| OpenAISDK[OpenAI SDK]
    Loop --> Runtime[tools/runtime.py<br/>ToolRuntime]
    Runtime --> ToolContract[tools/base.py<br/>Tool 契约]
    Runtime --> Perms[permissions/<br/>规则 + workspace + shell]
    Runtime --> ToolHooks[hooks/<br/>PreToolUse / PostToolUse]
    Runtime --> FS[文件读写]
    Runtime --> Shell[Shell 命令]
    Runtime --> Search[搜索工具]
    Runtime --> SkillTool[skill 工具]
    Runtime --> WebFetch[web_fetch]
    Runtime --> SubAgent[subagent.py<br/>子智能体]
    SubAgent -.->|fork-return| Loop
    Loop --> Memory[memory/<br/>记忆系统]
    Prompt --> Memory
    Prompt --> Skills[skill/<br/>技能系统]
    Runtime --> MCP[mcp_client.py<br/>MCP 集成]
    MCP --> ExtTools[外部工具服务器]
    Engine --> Session[session.py<br/>会话管理]
    Core --> UI[ui.py<br/>终端 UI]

    style Loop fill:#7c5cfc,color:#fff
    style Runtime fill:#e8e0ff
    style CLI fill:#e8e0ff
    style Memory fill:#ffe0e0
    style Skills fill:#ffe0e0
    style SubAgent fill:#e0ffe0
    style MCP fill:#e0f0ff
```

主线很清晰：**用户输入 → CLI → 会话引擎 → 事件流循环 → 模型决策 → ToolRuntime 执行 → 结果反馈 → 循环直到完成**

各组件职责：

- **`nano_code/__main__.py`**：解析命令行参数，提供交互式 REPL
- **`nano_code/agent/core.py`**：`Agent` 门面，负责初始化、对外 API 和事件渲染
- **`nano_code/agent/engine.py`**：一次用户提交的入口，负责 MCP 初始化、UserPromptSubmit/Stop hooks、会话保存
- **`nano_code/agent/loop.py`**：事件流主循环，负责调用模型、收集工具调用、把工具结果写回消息历史
- **`nano_code/agent/events.py`**：主循环对外产出的事件协议，CLI/UI 不再直接耦合后端细节
- **`nano_code/tools/base.py` / `nano_code/tools/registry.py` / `nano_code/tools/runtime.py`**：Tool 契约、注册表和统一执行管线
- **`nano_code/permissions/`**：权限规则、workspace 边界、protected path、shell 风险检测
- **`nano_code/hooks/`**：命令式 hooks 配置、匹配和执行
- **`nano_code/memory/` / `nano_code/skill/`**：记忆和技能模块，两者都能注入系统提示词或通过工具被调用
- **`nano_code/subagent.py`**：子智能体类型配置，当前规划能力通过只读 `plan` 子 agent 提供
- **`nano_code/mcp_client.py`**：MCP 协议客户端，通过标准输入输出上的 JSON-RPC 连接外部工具服务器
- **`nano_code/session.py`**：把对话历史写到磁盘，支持 `--resume` 恢复
- **`nano_code/ui.py`**：终端颜色和格式化输出

| 模块 | 职责 |
|------|------|
| `nano_code/agent/` | Agent 门面、事件流主循环、后端适配、循环状态 |
| `nano_code/tools/` | Tool 契约、schema 注册、内置工具适配、统一运行时 |
| `nano_code/permissions/` | deny/allow 规则、权限模式、workspace 和 shell 安全检查 |
| `nano_code/hooks/` | `UserPromptSubmit`、`PreToolUse`、`PostToolUse`、`Stop` hooks |
| `nano_code/sandbox/` | 可选沙箱后端配置与执行入口 |
| `nano_code/memory/` | 记忆存储、召回、渲染和压缩 |
| `nano_code/skill/` | 技能发现、激活、提示词注入和工具调用 |
| `nano_code/__main__.py` | CLI 参数、REPL、本地命令分流 |
| `nano_code/prompt.py` | 系统提示词构造：模板、项目规则、记忆/技能注入 |
| `nano_code/session.py` | 会话持久化 |

## 第一次读代码的路线

如果你是第一次看这个项目，不建议从 `agent.py` 第 1 行一直读到最后。那样很容易被流式输出、双后端、预算控制这些细节打断。更舒服的路线是按一次真实请求走：

1. 先看 `nano_code/__main__.py` 的 `main()`：命令行参数如何变成一个 `Agent` 实例。
2. 接着看 `run_repl()`：用户输入一行文字后，最终只是调用 `await agent.chat(text)`。
3. 跳到 `nano_code/agent/core.py` 的 `chat()`：这里消费事件流并把事件渲染到终端。
4. 看 `nano_code/agent/engine.py` 和 `nano_code/agent/loop.py`：这里才是一次请求的主干，包含 hooks、模型调用、工具调用和消息历史更新。
5. 工具真正怎么执行，去 `nano_code/tools/runtime.py` 看 `ToolRuntime`；权限为什么会拦截，接着看 `nano_code/permissions/policy.py`。
6. 如果好奇模型为什么知道有哪些工具、项目规则和记忆，回到 `nano_code/prompt.py` 的 `build_system_prompt()`。

这条线读完后，你再看记忆、技能、子智能体和 MCP，就不会觉得它们是额外魔法。它们本质上只是给主循环增加更多上下文或更多工具。

## 技术栈

当前项目只保留 Python 实现，下面所有运行命令和本项目源码路径均以 Python 版本为准。

#### Python

```
Python 3.11+         — 简洁易读
anthropic            — Anthropic 官方 SDK
openai               — OpenAI 兼容后端支持
```

没有框架、没有构建工具链，只有最基础的依赖。

## 快速开始

#### Python

```bash
git clone https://github.com/Windy3f3f3f3f/claude-code-from-scratch.git
cd claude-code-from-scratch
pip install -e .
export ANTHROPIC_API_KEY=sk-ant-xxx
nano-code "hello"
```

启动后：

```
  Mini Claude Code — A minimal coding agent

  Type your request, or 'exit' to quit.
  Commands: /clear /cost /compact /memory /skills

>
```

试试 `read nano_code/agent/loop.py and explain the main loop`。

### 其他选项

```bash
nano-code --yolo "run all tests"          # 跳过普通确认，但不绕过 deny/protected path
nano-code --accept-edits "refactor"       # 自动批准普通文件编辑
nano-code --dont-ask "check style"        # 需确认的操作自动拒绝
nano-code --thinking "analyze this bug"   # 启用 Extended Thinking
nano-code --resume                        # 恢复上次会话
nano-code --max-cost 0.50 --max-turns 20  # 预算控制
```

## 各章概览

| 章节 | nano-code 文件 | Claude Code 对应模块 |
|------|-----------------|---------------------|
| **Phase 1: 构建一个可用的编程智能体** | | |
| [1. 智能体循环](01-agent-loop.md) | `nano_code/agent/engine.py` + `nano_code/agent/loop.py` | 查询循环 |
| [2. 工具系统](02-tools.md) | `nano_code/tools/base.py` + `nano_code/tools/runtime.py` | Tool 抽象 + 内置工具 |
| [3. 系统提示词](03-system-prompt.md) | `nano_code/prompt.py` | 提示词模板与 CLAUDE.md 加载 |
| [4. CLI 与会话](04-cli-session.md) | `nano_code/__main__.py` + `nano_code/session.py` | CLI 入口与命令 |
| [5. 流式输出](05-streaming.md) | `nano_code/agent/backends.py` + `nano_code/agent/events.py` | API streaming 服务 |
| [6. 权限与安全](06-permissions.md) | `nano_code/permissions/` + `nano_code/tools/runtime.py` | `src/utils/permissions/` (52KB) |
| [7. 上下文管理](07-context.md) | `nano_code/agent/context.py` | `src/services/compact/` |
| **Phase 2: 进阶能力** | | |
| [8. 记忆系统](08-memory.md) | `nano_code/memory/` | memory 模块 |
| [9. 技能系统](09-skills.md) | `nano_code/skill/` | skills 模块 + SkillTool |
| [10. Plan Mode](10-plan-mode.md) | 历史设计记录，当前源码已删除全局 Plan Mode | `EnterPlanMode` / `ExitPlanMode` |
| [11. 多 Agent](11-multi-agent.md) | `nano_code/subagent.py` + `nano_code/tools/registry.py` | `src/tools/AgentTool/` |
| [12. MCP 集成](12-mcp.md) | `nano_code/mcp_client.py` | MCP client 服务 |
| [13. 架构对比](13-whats-next.md) | 全局对比 | 全局对比 |
| [15. 代码导读](15-code-reading-guide.md) | 全部 Python 文件 | 从一次请求串起所有模块 |

---

> **下一章**：从最核心的部分开始——智能体循环，这是整个编程智能体的心脏。

## 本章小结：这章应该建立什么心智模型

这一章最重要的不是记住每个文件名，而是先建立一个整体模型：**nano-code 是一个“模型决策 + 代码执行”的系统**。模型负责判断下一步要读文件、搜索、编辑还是运行命令；代码负责把这些动作变成受控工具调用，并在必要时做 hooks、权限检查、结果截断和会话保存。

实现上，这个模型落在三条线里。第一条是入口线：`__main__.py` 接收用户输入并调用 `Agent.chat()`。第二条是循环线：`agent/engine.py` 和 `agent/loop.py` 调 API、产出事件、执行工具、把结果塞回消息历史。第三条是能力线：`tools/`、`permissions/`、`hooks/`、`memory/`、`skill/`、`subagent.py`、`mcp_client.py` 给循环提供更多可用能力。

这章里的架构图可以反复回看。后面每一章其实都在回答同一个问题：为了让这个循环更好用、更安全、更能处理长任务，我们给它加了哪一层能力？
