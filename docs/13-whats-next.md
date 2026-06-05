# 13. 架构对比与下一步

## 完整架构对比

| 组件 | Claude Code | mini-claude | 差异 |
|------|------------|-------------|------|
| **智能体循环** | 7 种 continue reason | 只检查 tool_use | 简化循环控制 |
| **工具数量** | 66+ 工具 | 13 个工具（6 核心 + web_fetch + tool_search + skill + agent + 2 plan mode） | 去掉特化工具 |
| **工具执行** | 并发执行 + streaming 早期启动 | 并行执行 + streaming 早期启动 | 架构对齐 |
| **API 后端** | Anthropic only | Anthropic + OpenAI 兼容 | 多了 OpenAI |
| **系统提示词** | static/dynamic 分界 + API 缓存 | 无缓存优化 | 去掉缓存 |
| **权限系统** | 7 层 + AST 分析 + 8 级规则源 | 5 模式 + 规则配置 + 正则 + 确认 | 层次对齐 |
| **上下文管理** | 4 级压缩流水线 | 4 层（budget + snip + microcompact + 摘要） | 架构对齐 |
| **记忆系统** | 4 类型 + 语义召回 + MEMORY.md 索引 | 4 类型 + 语义召回 + MEMORY.md + 异步预取 | 架构对齐 |
| **技能系统** | 6 源 + 懒加载 + inline/fork | 2 源 + 预加载 + inline/fork | 去掉高级加载 |
| **多 Agent** | Sub-Agent + 自定义 + Coordinator + Swarm | Sub-Agent（3 内置 + 自定义） | 去掉 Coordinator/Swarm |
| **MCP 集成** | 动态工具发现 | McpManager + JSON-RPC over stdio | 架构对齐 |
| **预算控制** | USD/轮次/abort 三维预算 | USD + 轮次限制 | 去掉 abort signal |
| **编辑验证** | 14 步流水线 | 引号容错 + 唯一性 + diff 输出 | 保留核心步骤 |

## 文件映射表

| 当前 Python 文件 | Claude Code 对应模块 | 说明 |
|------------|-------------------|------|
| `mini_claude/agent.py` | 查询循环与 QueryEngine | Agent 循环 + 会话管理 |
| `mini_claude/tools.py` | Tool 抽象与内置工具目录 | 工具定义与执行 |
| `mini_claude/prompt.py` | prompts 与 CLAUDE.md 加载模块 | Prompt 构造 |
| `mini_claude/__main__.py` | CLI 入口与命令模块 | 入口与命令 |
| `mini_claude/ui.py` | `src/components/` (React/Ink 组件) | UI 渲染 |
| `mini_claude/session.py` | session storage 与 history 模块 | 会话持久化 |
| `mini_claude/memory.py` | memory 模块 + 系统 prompt 注入 | 记忆系统 |
| `mini_claude/skills.py` | skills 模块 + SkillTool | 技能系统 |
| `mini_claude/subagent.py` | `src/tools/AgentTool/` (built-in types) | 子 Agent 类型配置 |
| `mini_claude/mcp_client.py` | MCP client 服务模块 | MCP 客户端 |

## 我们没实现的

### Hooks（钩子系统）

Claude Code 有 25 种 hook 事件、6 种 hook 类型，可在工具执行前后插入自定义逻辑——拦截危险操作、记录审计日志、自动运行 lint 检查。它是 Claude Code 从"工具"变成"平台"的关键机制。

我们没实现的原因：核心挑战不在于"调一个函数"，而在于 hook 的发现与加载、错误隔离、stdin/stdout JSON 数据协议。这些工程细节约 500-800 行，但对理解 agent 原理没有帮助。

Hooks 的用途是把“工具执行前后发生什么”交给用户配置。例如运行 shell 前先检查命令是否符合团队策略，编辑文件后自动跑 formatter，工具失败后写审计日志。它和权限系统相关，但比权限更通用：权限只回答能不能做，Hook 可以做记录、改写、提示、阻止等多种动作。

### Coordinator / Swarm 多 Agent 模式

我们实现了 Sub-Agent（fork-return）。Claude Code 还有两种模式：**Coordinator** 把大任务拆分给多个专业 Agent，**Swarm** 让多个 Agent 对等通信、并行探索。两种模式解决的是单 Agent 上下文不够时的任务分解问题。

没实现的原因：核心挑战是任务分解准确性和 Agent 间通信协议设计，更多是 prompt engineering 问题而非代码架构问题。实现本身不复杂，但要真正好用需要大量 prompt 调优。

当前的 Sub-Agent 是最小可用的多智能体形态：发任务、等结果、合并结论。Coordinator / Swarm 则更像组织多个 worker 协作，需要考虑任务分配是否完整、worker 之间是否重复劳动、结果冲突时谁来裁决。这些问题不只靠代码结构解决，还依赖提示词和调度策略。

### LSP 集成

LSP 让 agent 在编辑文件后毫秒级获得类型错误反馈，而不需要等完整的编译/测试周期。在大型项目中，这能把修复一个 bug 所需的循环次数减少 30-50%。

没实现的原因：需要管理 LSP 服务器进程、实现客户端协议（初始化握手、能力协商、增量同步），1000+ 行且依赖对 LSP 协议的深入理解。通过 shell 命令（`python -m py_compile`、`python -m compileall`）获得错误反馈，对教程场景已经足够。

### Prompt Caching

Anthropic API 支持缓存系统提示词——Claude Code 把不变的部分（角色定义、工具规范）放前面，变化的部分（git 状态、当前文件）放后面，缓存命中可将输入 token 成本降低 90%。

没实现的原因：代码改动极小（20-30 行），但需要仔细设计提示词分区策略。如果你的 agent 要上线，这应该是第一个加上的优化。

### Bash AST 安全分析

Claude Code 用 tree-sitter 解析 shell 命令的 AST，进行 23 项静态安全检查，能分析出管道组合中的危险命令——这是纯正则做不到的。

没实现的原因：tree-sitter 是 C/C++ 原生库，依赖额外的原生编译环境。正则匹配覆盖了 80% 的常见危险模式，教程场景风险可接受。

## 渐进式增强路线图

### 第一阶段：性能与成本优化（1-2 天）

| 增强项 | 解决的问题 | 预计代码量 |
|--------|-----------|-----------|
| Prompt Caching | 重复发送系统提示词浪费 token | ~30 行 |

**Prompt Caching** 是投入产出比最高的优化：给系统提示词的静态部分加上 `cache_control: { type: "ephemeral" }` 标记，多轮对话中节省 50%+ 的输入 token 成本。

Prompt Caching 和第 3 章的系统提示词结构直接相关。只有把“长期不变的内容”和“每轮变化的内容”分开，缓存才容易命中。比如身份、工具使用规则、风险框架属于静态部分；Git 状态、当前日期、记忆召回结果属于动态部分。缓存优化看似是 API 参数，实际会反过来影响提示词组织方式。

### 第二阶段：可扩展性（3-5 天）

| 增强项 | 解决的问题 | 预计代码量 |
|--------|-----------|-----------|
| Hook 系统 | 定制 agent 行为需要改源码 | ~300 行 |
| Tool 类型系统 | switch/case 不能扩展到 20+ 工具 | ~200 行 |

核心转变是**从硬编码到插件化**。当前 switch/case 在 10 个工具时没问题，但超过 20 个就需要引入 Tool 接口（或 Python 的 Protocol/ABC），让每个工具成为独立模块。

### 第三阶段：可靠性与安全（1-2 周）

| 增强项 | 解决的问题 | 预计代码量 |
|--------|-----------|-----------|
| 7 种错误恢复策略 | 当前遇到错误直接崩溃 | ~400 行 |
| Bash AST 安全分析 | 正则匹配漏检复杂危险命令 | ~600 行 |

Claude Code 的查询循环模块有大量边缘情况处理：Prompt Too Long 时自动压缩重试、API 过载时指数退避、工具失败时把错误反馈给模型让它自修复。

### 第四阶段：高级 Agent 能力（2-4 周）

| 增强项 | 解决的问题 | 预计代码量 |
|--------|-----------|-----------|
| Coordinator 模式 | 大任务超出单 Agent 上下文容量 | ~500 行 |
| Swarm 模式 | 探索性任务需要多路径并行 | ~600 行 |
| LSP 集成 | 类型错误只能通过编译发现 | ~1000 行 |

## 扩展方向

### 1. Hooks 系统

最简单的方案是 command hook——在 `executeTool` 前 spawn shell 子进程，通过 stdin JSON 传入工具信息，解析 stdout JSON 决定 allow/deny。

配置示例：
```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "run_shell", "command": "./hooks/pre-shell.sh" }
    ]
  }
}
```

核心逻辑：遍历匹配的 hook，spawn 子进程传 JSON，根据 `{"action": "allow"}` / `{"action": "deny", "reason": "..."}` 决定是否继续执行。约 300 行，最耗时的是子进程的超时和 crash 处理。

### 2. 错误自修复

把工具执行错误作为工具结果反馈给模型，而不是中断循环。模型经常能自己修复：路径拼错换路径、命令参数错了改参数。

```python
async def _execute_tool_call(self, name: str, inp: dict) -> str:
    try:
        return await execute_tool(name, inp, self._read_file_state)
    except Exception as exc:
        # 错误不打断智能体循环，而是作为 tool_result 反馈给模型。
        # 下一轮模型可以读取错误、调整参数、重新调用工具。
        return f"Error executing tool {name}: {exc}"
```

约 50-80 行，但能显著提升 agent 实际可用性——这是 Claude Code 最聪明的设计之一。

## 核心洞察

**1. Agent 的本质是一个 while 循环**

```
while true:
    response = llm.call(messages)
    if no tool_calls in response: break
    for tool_call in response.tool_calls:
        result = execute(tool_call)
        messages.append(result)
```

所有的复杂性——权限、上下文管理、记忆、多 Agent——都是围绕这个循环的增强和防护。

**2. 提示词是最便宜的代码**

系统提示词里的一句话，效果等同于一个 if 语句，实现成本是 0 行代码。agent 开发中很多行为问题的最优解不是写更多代码，而是写更好的提示词——更灵活、更容易修改、非技术人员也能读懂。

**3. 工具设计决定能力上限**

让模型做它擅长的（理解意图、生成代码），让工具做模型不擅长的（精确字符串匹配、文件系统操作、进程管理）。`edit_file` 是典型：模型生成要替换的内容，工具负责在文件中精确定位和替换。

**4. 上下文管理是 agent 的"记忆力"**

上下文管理之于 agent，就像内存管理之于操作系统——用有限资源提供"无限"错觉。4 层压缩流水线让 agent 在有限窗口中保持对长对话的记忆。

**5. 安全不是事后补丁**

权限检查是 agent 循环的一个步骤，不是外挂的 middleware。没有任何工具可以绕过它。更重要的是 fail-closed 设计：新工具如果忘记声明权限级别，被自动当作"需要确认"处理——系统通过默认值保证安全。

这一点对扩展项目尤其重要。很多原型是在工具能跑之后才补安全，结果后来发现每个工具都有自己的绕过路径。更稳的做法是把权限检查放在统一入口：所有工具调用都必须经过同一条路。当前 Python 版的 `_execute_tool_call()` 和 `check_permission()` 就是在建立这个入口。

**6. 从 3000 行到 50 万行的差距在于边缘情况**

Claude Code 多出来的代码大多是：各运行环境兼容性、网络和 API 不可靠性、用户输入多样性、企业级审计和访问控制。这些"无聊"的代码不会出现在架构图中，却是工具能否在真实世界可靠运行的关键。从原型到产品，80% 的距离在这里。

**7. LLM 与代码的协作边界**

构建编程智能体最核心的能力：设计好模型和代码之间的协作边界。哪些让模型决定，哪些让代码决定——边界划得好，智能体既灵活又可靠。我们在教程里每个设计决策都体现了这个原则：模型决定“做什么”，代码确保“安全地做”。

## 交叉引用

想深入了解 Claude Code 各模块的设计原理？参考兄弟项目的详细文档：

| 主题 | 本教程 | how-claude-code-works |
|------|--------|----------------------|
| 智能体循环 | [Ch1: 智能体循环](01-agent-loop.md) | [系统主循环](https://windy3f3f3f3f.github.io/how-claude-code-works/#/docs/02-agent-loop) |
| 工具系统 | [Ch2: 工具系统](02-tools.md) | [工具系统](https://windy3f3f3f3f.github.io/how-claude-code-works/#/docs/04-tool-system) |
| 上下文管理 | [Ch7: 上下文管理](07-context.md) | [上下文工程](https://windy3f3f3f3f.github.io/how-claude-code-works/#/docs/03-context-engineering) |
| 权限安全 | [Ch6: 权限与安全](06-permissions.md) | [权限与安全](https://windy3f3f3f3f.github.io/how-claude-code-works/#/docs/10-permission-security) |
| 记忆系统 | [Ch8: 记忆系统](08-memory.md) | [记忆系统](https://windy3f3f3f3f.github.io/how-claude-code-works/#/docs/08-memory-system) |
| 技能系统 | [Ch9: 技能系统](09-skills.md) | [技能系统](https://windy3f3f3f3f.github.io/how-claude-code-works/#/docs/09-skills-system) |
| Plan Mode | [Ch10: Plan Mode](10-plan-mode.md) | — |
| 多 Agent | [Ch11: 多 Agent](11-multi-agent.md) | [多 Agent 架构](https://windy3f3f3f3f.github.io/how-claude-code-works/#/docs/07-multi-agent) |
| MCP 集成 | [Ch12: MCP 集成](12-mcp.md) | — |

---

## 结语

约 3800 行 Python 代码，覆盖了一个编程智能体的核心组件和进阶能力：

**Phase 1 — 核心组件：** 智能体循环、工具系统（13 工具 + mtime 防护 + 延迟加载 + 并行执行）、系统提示词（Markdown 模板 + @include + 环境注入）、CLI / 会话（REPL + JSON 持久化）、流式输出（Anthropic + OpenAI 双后端 + streaming 工具执行）、权限安全（5 模式 + 声明式规则 + 正则 + 确认）、上下文管理（4 层压缩 + 大结果持久化）

**Phase 2 — 进阶能力：** 记忆系统（语义召回 + 异步预取）、技能系统（inline/fork 双模式）、Plan Mode（只读规划 + 4 选项审批）、多 Agent（Sub-Agent + 3 内置类型 + 自定义）、MCP 集成（JSON-RPC over stdio）、预算控制

Claude Code 50 万行里的大量代码是边缘情况处理和企业级可靠性。但核心 agent 能力——理解用户意图 → 调用工具操作代码 → 迭代直到完成——就是这 ~3400 行的事。

现在你有了一个功能丰富的编程智能体，也理解了它背后每一块代码的设计意图。去扩展它吧。

## 本章小结：如何理解“没实现的部分”

这一章列出的 Hooks、Coordinator、LSP、Prompt Caching、Bash AST 安全分析，并不是另一个世界的东西。它们都是围绕同一个主循环继续增强：Hooks 让工具执行前后可插入用户逻辑；Coordinator 让任务能被多个智能体拆分；LSP 让代码理解更精确；Prompt Caching 降低重复上下文成本；Bash AST 分析让 shell 权限更可靠。

如果要扩展当前 Python 版，建议先判断你要解决的是哪类问题。想提高可扩展性，优先做 Hooks 或插件化工具注册；想提高代码理解，优先接 LSP 或更强的索引；想提高安全，优先替换正则命令检测；想降低成本，优先做提示词缓存和更细的上下文压缩。

相关概念是“复杂度预算”。完整 Claude Code 的大量代码不是为了展示主干，而是为了覆盖真实用户环境里的边界情况。教程项目的价值在于把主干讲清楚。你扩展时也应该保持这个原则：先确认问题真实存在，再引入相应复杂度。
