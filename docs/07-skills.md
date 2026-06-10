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
| 1 | 会话启动 | skill 名称、描述、调用方式 |
| 2 | 调用时 | 完整 `SKILL.md` body 和参数渲染 |
| 3 | 模型主动读 | skill 目录下 supporting files |

这样避免把所有 skill 正文一次性塞进上下文。

## 4. inline vs fork

- `context: inline`：渲染后的提示词注入主 Agent 当前对话。
- `context: fork`：创建独立子 Agent 执行，只把最终结果带回。

fork skill 本质上走子 Agent 路径，由 `AgentSession` 装配和运行。

## 5. 参数替换

支持：

- `$ARGUMENTS` / `${ARGUMENTS}`
- `$0` / `$1`
- `$ARGUMENTS[0]`
- `${CLAUDE_SKILL_DIR}`

如果用户传了参数但正文没有占位符，参数会追加到 `ARGUMENTS:` 区块。

## 6. ActiveSkillManager

Compact 后，已激活 skill 的指令可能从消息历史里消失。`ActiveSkillManager.build_context()` 会重新注入 active skill 上下文。

`disallowed_tools()` 聚合 active skill 声明的禁用工具，传给 `ToolRegistry.active_definitions()`，从工具定义层限制模型能看到的工具。

## 7. 代码导读

```
cli/core/skills/registry.py
cli/core/skills/runtime.py
cli/core/skills/prompt.py
cli/session.py::invoke_skill
```
