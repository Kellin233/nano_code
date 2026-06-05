# 15. 代码导读：从一次请求看完整项目

前面的章节按能力拆开讲：工具、提示词、权限、记忆、技能、MCP。真正读代码时，人的脑子通常不是这么工作的。你会先问：“我在终端输入一句话之后，到底发生了什么？”

本章就按这个问题来走。它不追求覆盖每一行，而是帮你建立一张能反复回来的地图。以后你改功能、排 bug、扩展工具时，可以先回到这张地图上定位。

## 先把项目想成三层

`mini_claude` 不是一个大而全的框架，它更像三层小系统叠在一起：

| 层级 | 负责什么 | 主要文件 |
|------|----------|----------|
| 入口层 | 接收用户输入、解析参数、展示终端交互 | `__main__.py`、`ui.py`、`session.py` |
| 循环层 | 调模型、识别工具调用、执行工具、回灌结果 | `agent.py` |
| 能力层 | 提供工具、提示词、记忆、技能、子智能体、MCP | `tools.py`、`prompt.py`、`memory.py`、`skills.py`、`subagent.py`、`mcp_client.py` |

如果你读代码时迷路了，先判断当前函数属于哪一层。入口层不应该知道工具细节；能力层不应该直接控制对话循环；循环层负责把它们串起来。

这个分层不是为了画图好看，而是为了定位问题。用户输入没进入模型，先查入口层；工具执行结果不对，先查能力层；模型反复调用工具停不下来，先查循环层。按层定位能避免一上来就陷进 `agent.py` 的所有细节里。

## 启动：命令行如何变成 Agent

入口在 `mini_claude/__main__.py`。

`parse_args()` 只做一件事：把命令行参数变成结构化配置。比如：

```bash
mini-claude --plan --model claude-sonnet-4-5 "分析这个项目"
```

会影响这些状态：

- `--plan` 让权限模式初始为 `plan`
- `--model` 决定 `Agent` 调哪个模型
- prompt 参数存在时走 one-shot，不存在时进入交互式循环

`main()` 创建 `Agent` 时，会把这些配置传进去。此时还没有真正调用模型，只是准备好一个能工作的对象。

交互式模式的核心在 `run_repl()`：它循环读取用户输入，处理 `/clear`、`/cost`、`/memory`、`/skills`、`/plan` 这些本地命令。只要不是本地命令，最后都会落到：

```python
await agent.chat(user_input)
```

这句就是从入口层进入循环层的门。

如果你要改启动参数、增加 REPL 命令、调整 Ctrl+C 行为，基本都在入口层完成。判断标准很简单：如果改动不需要理解模型消息协议，也不需要知道工具内部怎么执行，那它大概率属于 `__main__.py` 或 `ui.py`。

## 第一跳：`Agent.chat()`

`agent.py` 的 `chat()` 是公开入口。它做的事情不多，但每一步都很关键：

1. 清掉上一次的 `_aborted` 标记。
2. 首次对话时连接 MCP 服务器，并把 MCP 工具追加到当前工具列表。
3. 根据 `use_openai` 选择 `_chat_openai()` 或 `_chat_anthropic()`。
4. 主智能体执行完后打印分隔线并自动保存会话。

这也解释了为什么 MCP 是“懒连接”：启动 CLI 不一定要用外部工具，只有真正开始聊天时才连接，避免用户只是问一句普通问题也要等服务器启动。

## 主循环：模型说下一步做什么

以 `_chat_anthropic()` 为例，它的骨架可以简化成：

```python
self._anthropic_messages.append({"role": "user", "content": user_message})
await self._check_and_compact()

while True:
    self._run_compression_pipeline()
    response = await self._call_anthropic_stream()
    tool_uses = [b for b in response.content if b.type == "tool_use"]
    self._anthropic_messages.append(assistant_message)

    if not tool_uses:
        break

    tool_results = []
    for tool_use in tool_uses:
        result = await self._execute_tool_call(tool_use.name, tool_use.input)
        tool_results.append(tool_result)

    self._anthropic_messages.append({"role": "user", "content": tool_results})
```

这里最容易误解的是退出条件。代码没有检查“任务是否完成”，它只检查“模型这次有没有继续调用工具”。没有工具调用，就认为模型要直接回答用户，于是退出循环。

这就是智能体的核心边界：模型负责决定做什么，代码负责确保每个动作按协议、权限和上下文规则执行。

## 工具调用：为什么分成两层

模型看到的工具来自 `tools.py` 的 `tool_definitions`。但真正执行时不是所有工具都直接进入 `tools.execute_tool()`。

`agent.py` 的 `_execute_tool_call()` 会先处理特殊工具：

- `enter_plan_mode` / `exit_plan_mode`：需要修改 `Agent` 的权限状态和 plan 文件路径。
- `agent`：需要创建子智能体，还要把 token 用量合并回父智能体。
- `skill`：inline 技能返回提示词，fork 技能会创建子智能体。
- `mcp__server__tool`：需要转发给 MCP 管理器。

剩下的普通工具才进入 `tools.execute_tool()`，例如 `read_file`、`edit_file`、`grep_search`、`run_shell`、`web_fetch`。

这个分层很实用：普通工具保持无状态，容易测试；需要访问会话状态的工具留在 `Agent` 内部。

新增功能时可以用这个标准判断放哪里：如果函数只依赖输入参数并返回字符串，放 `tools.py`；如果它需要修改权限模式、读取会话 token、创建子智能体、访问 MCP manager，就放 `agent.py` 的特殊分支。这个边界能防止 `tools.py` 逐渐变成另一个巨大的全局状态管理器。

## 编辑保护：为什么写文件前要先读

`tools.execute_tool()` 里有一段看似啰嗦的逻辑：`write_file` 和 `edit_file` 前必须检查 `read_file_state`。

它解决的是一个很现实的问题：模型可能基于旧内容写文件。如果用户或另一个进程刚刚改过同一个文件，模型直接覆盖就会丢改动。

当前实现的规则是：

1. `read_file` 成功后，把文件绝对路径和修改时间记录到 `Agent._read_file_state`。
2. `write_file` / `edit_file` 发现目标文件已存在时，必须先在这个字典里找到记录。
3. 如果当前修改时间和读取时不同，返回警告，让模型重新读取。
4. 写入或编辑成功后，更新这个文件的修改时间。

这不是为了为难模型，而是让模型的每次编辑都基于它真的看过的文件状态。

## 权限：工具执行前的闸门

权限检查在 `agent.py` 的工具循环里发生，真正规则在 `tools.py` 的 `check_permission()`。

你可以把它想成四步：

1. 如果是 `bypassPermissions`，直接允许。
2. 如果配置文件 `.claude/settings.json` 或 `~/.claude/settings.json` 有 allow/deny 规则，先匹配规则。
3. 如果当前是 `plan` 模式，只允许读工具，以及写 plan 文件本身。
4. 对 `run_shell` 这类危险操作做正则检测，必要时返回 `confirm`。

返回值不是布尔值，而是一个动作：`allow`、`deny` 或 `confirm`。这样调用方能区分“直接拒绝”和“询问用户后也许允许”。

## 系统提示词：模型到底看到了什么

`prompt.py` 的 `build_system_prompt()` 会把多种信息拼成一段完整系统提示词：

- 静态规则：身份、工具使用策略、编辑偏好、安全边界。
- 环境信息：当前目录、操作系统、日期、Git 状态。
- 项目说明：从当前目录向上收集 `CLAUDE.md`，并解析 `@include`。
- 规则目录：加载 `.claude/rules/*.md`。
- 记忆说明：来自 `memory.py` 的 `build_memory_prompt_section()`。
- 技能说明：来自 `skills.py` 的 `build_skill_descriptions()`。
- 延迟工具提示：告诉模型哪些工具可以通过 `tool_search` 激活。

所以模型不是“凭空知道项目”。它能表现得像在项目里工作，是因为每次 API 调用前，代码都把这些上下文重新整理给它。

## 上下文压缩：为什么不能随便删消息

`agent.py` 里有两类压缩：

- `_run_compression_pipeline()`：不调用模型，直接裁剪或替换旧工具结果。
- `_check_and_compact()`：必要时调用模型，把整段对话总结成较短摘要。

要特别注意 `_check_and_compact()` 的调用时机。它放在用户消息刚进入历史、下一次 API 调用之前。此时最后一条消息是普通用户文本，可以安全地拿掉最后一条去总结前文，再把这条用户消息接回去。

如果在工具循环中间压缩，最后一条可能是工具结果。切掉它会让前一条 assistant 消息里的 `tool_use` 失去对应的 `tool_result`，API 会直接拒绝。这也是第 7 章反复强调“回合边界”的原因。

## 记忆：长期信息不放在会话里

会话历史适合保存“这次对话发生了什么”，不适合保存长期偏好。记忆系统在 `memory.py`，存储目录由当前项目路径 hash 决定，避免不同项目的记忆混在一起。

一条记忆大致包含：

- `name`：短名字
- `description`：给模型判断是否相关的描述
- `type`：偏好、项目事实、行为反馈等类型
- 正文：真正要记住的内容

普通注入只会给模型一个记忆清单。真正需要详细内容时，`start_memory_prefetch()` 会用旁路查询异步判断哪些记忆相关，再把相关记忆作为 `<system-reminder>` 注入下一轮上下文。

这里的设计重点是“别把所有记忆都塞给模型”。记忆越多，越需要先筛选。

## 技能：可复用的提示词模块

技能系统在 `skills.py`。项目会扫描两个目录：

- `.claude/skills/*/SKILL.md`
- `~/.claude/skills/*/SKILL.md`

每个 `SKILL.md` 前面可以有 YAML 元数据头，正文是技能提示词。`context` 决定执行方式：

- `inline`：把技能提示词返回给当前对话，让主模型继续执行。
- `fork`：创建一个子智能体执行技能，只把最终结果带回主对话。

`fork` 适合读很多文件、跑很多工具的技能。这样主对话不会被大量中间工具结果塞满。

## 子智能体：同一个 Agent，换一套配置

子智能体不是新框架。`subagent.py` 只负责提供不同类型的系统提示词和工具列表，真正运行时还是创建一个新的 `Agent` 实例。

内置类型有三个：

- `explore`：只读探索，适合搜索代码和收集事实。
- `plan`：只读规划，适合产出结构化方案。
- `general`：完整工具集，但不能再调用 `agent`，避免递归创建。

父智能体调用 `_execute_agent_tool()` 时，会创建子 `Agent`，传入 `custom_system_prompt`、`custom_tools` 和 `is_sub_agent=True`。子智能体的输出不直接打印，而是进入 `_output_buffer`，最后作为字符串返回给父智能体。

这就是 fork-return：分出去独立做事，做完只带结果回来。

## MCP：把外部工具伪装成普通工具

`mcp_client.py` 做三件事：

1. 根据配置启动 MCP 服务器进程。
2. 用 JSON-RPC 完成初始化和工具发现。
3. 把外部工具改名为 `mcp__server__tool`，再转换成和内置工具一样的 schema。

对主循环来说，MCP 工具没有特殊协议。模型调用 `mcp__demo__hello`，`_execute_tool_call()` 看到这是 MCP 工具，就交给 `McpManager.call_tool()`。返回值仍然是一段文本，照样包装成工具结果喂回模型。

这就是 MCP 的好处：扩展工具能力时，不需要改智能体循环。

## 会话：保存的是消息历史

`session.py` 很小，因为它只做 JSON 文件读写。`Agent._auto_save()` 会在主智能体每次 `chat()` 结束后保存：

- session id
- Anthropic 消息历史
- OpenAI 消息历史
- token 统计
- 模型信息

`--resume` 恢复时，`restore_session()` 把这些数据塞回 `Agent`。这不是完整的进程快照，MCP 连接、临时审批缓存、当前输出 buffer 这类运行时状态不会恢复。恢复的是对话上下文本身。

## 修改代码时的定位方法

如果你想加一个普通工具：

1. 在 `tools.py` 的 `tool_definitions` 添加 schema。
2. 写一个 `_your_tool(inp)` 函数。
3. 在 `execute_tool()` 的 `handlers` 里注册。
4. 如果工具会修改文件或执行命令，检查 `check_permission()` 是否需要新增规则。

如果你想加一个需要会话状态的工具：

1. 仍然先在 `tool_definitions` 添加 schema。
2. 在 `agent.py` 的 `_execute_tool_call()` 里加特殊分支。
3. 把状态放在 `Agent` 实例上，而不是放进 `tools.py`。

如果你想改模型行为：

1. 先看 `prompt.py` 的 `SYSTEM_PROMPT_TEMPLATE`。
2. 如果是项目级规则，优先写 `CLAUDE.md` 或 `.claude/rules/*.md`，不要急着改代码。
3. 如果是可复用流程，考虑做成 `.claude/skills/*/SKILL.md`。

如果你想排查“模型为什么没调用某个工具”：

1. 看这个工具是否在 `get_active_tool_definitions()` 返回列表里。
2. 如果是 deferred 工具，确认模型是否先调用了 `tool_search`。
3. 看系统提示词里是否说明了工具使用场景。
4. 看工具 schema 的描述是否足够清楚，参数名是否容易误解。

## 最后再看 `agent.py`

`agent.py` 长，是因为它把许多横切逻辑放在一个类里：双后端、流式输出、工具编排、压缩、预算、Plan Mode、子智能体。读它时不要从头到尾硬啃，可以按这几个块看：

| 代码区域 | 先看什么 |
|----------|----------|
| 初始化 | `__init__()`：哪些状态属于一个会话 |
| 对外入口 | `chat()`、`run_once()`：主智能体和子智能体怎么复用同一套逻辑 |
| 压缩 | `_check_and_compact()`、`_run_compression_pipeline()` |
| 工具 | `_execute_tool_call()`、`_execute_skill_tool()`、`_execute_agent_tool()` |
| 规划模式 | `toggle_plan_mode()`、`_execute_plan_mode_tool()` |
| Anthropic 后端 | `_chat_anthropic()`、`_call_anthropic_stream()` |
| OpenAI 兼容后端 | `_chat_openai()`、`_call_openai_stream()` |

理解这些块之后，整个项目会变得很小：一个循环，一组工具，一段提示词，加上一些让它更可靠的保护层。

---

> 回到前面的章节时，可以把本章当地图用：想知道某个能力为什么存在，就看对应章节；想知道它在代码里怎么串起来，就回到这里。

## 本章小结：怎么把这张地图用起来

这章最适合在你准备改代码前看。比如你想加一个新工具，不要直接打开 `agent.py` 搜索工具名，而是先判断它属于普通无状态工具，还是需要访问会话状态的特殊工具。前者改 `tools.py`，后者需要在 `Agent._execute_tool_call()` 里加分支。

如果你在调 bug，也可以按请求路径倒推。用户输入没生效，先看 `__main__.py` 和 REPL 分流；工具没出现，查 `tool_definitions`、`get_active_tool_definitions()` 和系统提示词；工具调用了但没执行，查权限和 `_execute_tool_call()`；结果回到模型后行为不对，再查消息历史和上下文压缩。

相关概念是“按边界定位”。这个项目虽然文件不多，但每个文件有明确责任：入口、循环、工具、提示词、记忆、技能、子智能体、MCP。遇到问题时先找边界，再看实现，效率会比从头读完整个仓库高很多。
