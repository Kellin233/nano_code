# 技能系统

## 1. 为什么需要 Skills

“写 commit message”、“做代码审查”、“生成发布说明”这类任务都有固定提示词模板。Skills 把这些模板放进 `.md` 文件，用户或模型用名字调用即可。

Skills 是应用层能力，位于 `cli/core/skills/`。它们不是插件，不执行新代码，不直接扩展工具；它们主要是提示词模板和上下文管理。

## 2. 文件结构

```
cli/core/skills/
├── __init__.py
├── types.py      # SkillDefinition、ActiveSkill、SkillInvocationResult
├── registry.py   # 扫描 .claude/skills/、发现、缓存、查找
├── runtime.py    # SkillInvocation + ActiveSkillManager
└── prompt.py     # build_skill_descriptions()
```

## 3. 三层阶段式披露

| 层 | 时机 | 内容 |
|:--:|------|------|
| 1 | 会话启动 | skill 名称、描述、`when_to_use`、调用方式 |
| 2 | 调用时 | 完整 `SKILL.md` body 和参数渲染 |
| 3 | 模型主动读 | skill 目录下 supporting files |

这样避免把所有 skill 正文一次性塞进上下文。

发现路径是：

```text
~/.claude/skills/<skill>/SKILL.md
./.claude/skills/<skill>/SKILL.md
```

项目级同名 skill 覆盖用户级。Discovery 阶段只读取 frontmatter，不读取正文；正文由 `SkillInvocation` 在调用时懒加载。

三层披露的维护意义：

- metadata 层帮助模型知道“有哪些 skill 可以调用”，但不把所有正文塞进 prompt。
- invocation 层把当前真正需要的 `SKILL.md` 渲染成 prompt，包含参数替换结果。
- supporting files 层保持完全按需，模型必须用 `read_file` 显式读取，不会隐式加载整个 skill 目录。

这让 skill 数量可以增长，而不会线性增加每次 provider call 的上下文成本。

## 4. inline vs fork

- `context: inline`：渲染后的提示词注入主 Agent 当前对话。
- `context: fork`：创建独立子 Agent 执行，只把最终结果带回。

inline skill 会进入 `ActiveSkillManager`，在后续上下文压缩后仍可恢复。fork skill 不加入主会话 active skill，而是由 `AgentSession` 调用 `agent` 工具路径创建子 Agent；如果 skill frontmatter 写了 `agent: explore`，就使用对应子 Agent 类型，否则使用 `general`。

skill frontmatter 中的 `allowed_tools` 会传给 fork 子 Agent，并在 inline skill 激活期间收窄主会话工具白名单。多个 active skill 同时声明 `allowed_tools` 时取交集；`disallowed_tools` 永远优先。

常用 frontmatter：

| 字段 | 作用 |
|------|------|
| `name` / `description` / `when_to_use` | 会话启动时展示的 metadata |
| `context` | `inline` 或 `fork` |
| `agent` | fork skill 使用的子 Agent 类型，默认 `general` |
| `allowed_tools` / `allowed-tools` | skill 生效期间允许的工具白名单 |
| `disallowed_tools` / `disallowed-tools` | skill 生效期间隐藏并拒绝的工具 |
| `user_invocable` / `user-invocable` | 是否允许用户用 `/<skill>` 调用，默认 true |
| `disable_model_invocation` / `disable-model-invocation` | 是否禁止模型通过 `skill` 工具调用 |
| `argument_hint` / `argument-hint` | TUI/metadata 中展示的参数提示 |

## 5. 调用链路

模型调用 skill：

```text
skill tool
  → AgentSession._execute_skill_tool()
  → SkillInvocation.invoke(skill_name, args, invoked_by="model")
  → registry.get()
  → render_prompt()
  → inline: ActiveSkillManager.record() + 返回渲染提示词
  → fork: AgentSession._execute_agent_tool() 创建子 Agent
```

用户调用 skill：

```text
/<skill> args
  → TUI command
  → AgentSession.invoke_skill(invoked_by="user")
  → user_invocable 检查
  → inline: active skill 记录后直接 chat(rendered_prompt)
  → fork: 子 Agent 执行后返回结果
```

inline skill 会持续影响当前会话，直到 `/clear` 或 active skill 被预算/数量修剪。fork skill 不污染主会话 active skill，只把子 Agent 结果返回。

调用限制的实际效果：

- `user_invocable=false`：用户不能用 `/<skill>` 调用，但模型仍可通过 `skill` 工具调用，除非同时禁止模型调用。
- `disable_model_invocation=true`：模型不能通过 `skill` 工具调用，但用户命令仍可用，除非 `user_invocable=false`。
- `allowed_tools`：inline skill 激活期间收窄主会话工具；fork skill 创建子 Agent 时作为 task allowlist。
- `disallowed_tools`：优先级高于 allowed，用于隐藏并拒绝危险或不适合当前 skill 的工具。

## 6. 参数替换

支持：

- `$ARGUMENTS` / `${ARGUMENTS}`
- `$0` / `$1`
- `$ARGUMENTS[0]`
- `${CLAUDE_SKILL_DIR}`

如果用户传了参数但正文没有占位符，参数会追加到 `ARGUMENTS:` 区块。

## 7. ActiveSkillManager

Compact 后，已激活 skill 的指令可能从消息历史里消失。`ActiveSkillManager.build_context()` 会重新注入 active skill 上下文。

`allowed_tools()` 聚合 active skill 声明的工具白名单并取交集，和 `RuntimeConfig.allowed_tools` 再取交集。`disallowed_tools()` 聚合 active skill 声明的禁用工具。二者同时传给 `ToolRegistry.active_definitions()` 和 `ToolRuntime`，因此限制同时作用在模型可见 schema 和真实执行边界。

Active skill 有预算和数量上限：默认最多保留 8 个 active skill，单个 skill 恢复上下文约 5000 tokens，总恢复预算约 25000 tokens。超预算时截断或跳过，避免 compact 后把上下文重新撑爆。

Active skill 生命周期：

```text
inline skill invoke
  → ActiveSkillManager.record()
  → 后续 tool definitions 受 allowed/disallowed tools 影响
  → Context Compact 后 build_context() 恢复 skill 指令
  → /clear 清空 active skills
  → 超过 max_active 或预算时修剪旧 skill
```

这意味着 inline skill 不是一次性提示词片段，而是会话内持续生效的运行约束。维护者修改 active skill 逻辑时，要同时检查 schema 暴露和 ToolRuntime 执行边界。

## 8. 设计决策

### 为什么 skill 不是插件

Skill 只提供提示词模板、frontmatter metadata 和 active 上下文恢复。它不加载 Python 代码，也不注册新工具；需要扩展工具时应使用 Extension 或 MCP。

### 为什么正文懒加载

会话启动只展示 skill metadata。完整 `SKILL.md` 正文和 supporting files 只有在真正调用或模型主动读取时进入上下文，避免大量未使用 skill 消耗 token。

### 为什么 allowed_tools 会收窄执行层

Skill 的工具限制如果只影响 schema，可被历史 schema 或异常 tool call 绕过。因此 active skill 的 allowed/disallowed tools 同时传给 `ToolRegistry.active_definitions()` 和 `ToolRuntime`。

## 9. Benchmark 覆盖

`benchmarks/local-fixture` 当前没有专门 skill case，但它覆盖了 skills 依赖的公共合同：

- allowed tools 在模型可见 schema 和 runtime 执行层都必须生效。
- context compact 后恢复上下文不能绕过工具限制。
- fork skill 走子 Agent 路径，应继续遵守父运行白名单与 task/skill 白名单交集。

新增 skill benchmark 时应覆盖 inline skill 的 active 恢复、fork skill 的工具边界、以及 `disallowed_tools` 优先级。

面试式理解点：

- Skill 与 Extension 的区别：Skill 是 prompt 和上下文，Extension 是进程内代码扩展。
- Skill 与 sub-agent 的关系：fork skill 通过子 Agent 执行，inline skill 留在主会话。
- Skill 工具限制为什么要双层执行：只隐藏 schema 不够，runtime 仍必须拒绝越界 tool call。

## 10. 代码导读

```
cli/core/skills/registry.py
cli/core/skills/runtime.py
cli/core/skills/prompt.py
cli/session.py::invoke_skill
```
