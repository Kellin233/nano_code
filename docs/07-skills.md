# 技能系统

## 1. 为什么需要 Skills

"写 git commit"、"做代码审查"——这些任务每次都需要一段特定的提示词。如果让用户手打长 prompt，效率低、不一致、容易遗漏关键步骤。Skills 把常用提示词模板化——用户或模型调用一个名字就触发完整流程。

Skills 不是"插件"——不引入新代码，不扩展工具。它们只是提示词模板。安全边界仍由工具白名单和权限控制。

## 2. 核心概念

### 2.1 三层阶段式披露

只在需要时给模型刚好够用的上下文：

| 层 | 时机 | 内容 | token 成本 |
|:--:|------|------|:--:|
| 1 | 会话启动 | 名称、一句话描述、调用方式 | 每 skill 一行 |
| 2 | 调用时 | 完整 SKILL.md body + 参数渲染 | 按需 |
| 3 | 模型主动读 | supporting files（skill 目录下文件） | 按需，通过 read_file |

为什么不在启动时加载所有 skill 正文？上下文窗口有限——一个项目几十个 skill，全部加载浪费 token。metadata 足够模型判断"该不该用"。

### 2.2 inline vs fork

`context: inline`（默认）：渲染后的提示词注入主 Agent 当前对话——适合轻量任务。`context: fork`：创建独立子 Agent 执行——适合重量任务，不污染主上下文。

### 2.3 参数替换

`$ARGUMENTS`/`${ARGUMENTS}`→完整参数字符串。`$0`/`$1` 或 `$ARGUMENTS[0]`→按 shell 风格拆分后的第 N 个参数。`${CLAUDE_SKILL_DIR}`→skill 所在目录路径。如果传了参数但正文没用占位符→参数追加到 `ARGUMENTS:` 区块。

### 2.4 Active Skill 管理

compact 压缩后 skill 指令丢失→`ActiveSkillManager.build_context()` 重新注入。最多 8 个 active，每 skill 最多 5000 token，总计 25000 token。`disallowed_tools()` 聚合所有 active skill 的禁用工具列表→传给 `ToolRegistry.active_definitions()`。

## 3. 总体设计

```
capabilities/skills/
├── types.py      # SkillDefinition、ActiveSkill、SkillInvocationResult
├── registry.py   # SkillRegistry：扫描 .claude/skills/、发现、缓存、查找
├── runtime.py    # SkillInvocation + ActiveSkillManager（invocation+active 合并）
└── prompt.py     # build_skill_descriptions()：system prompt 中的 skill 列表
```

## 4. 详细设计

**`registry.py`**：`SkillRegistry` 扫描 `~/.claude/skills/` 和 `./.claude/skills/`。项目级覆盖用户级。发现阶段只读 frontmatter 不读正文。`get_skill_by_name()` 快捷入口。`discover_skills()` 全量列出。

**`runtime.py`**：`SkillInvocation.invoke()` 权限检查（user-invocable/disable-model-invocation）→懒加载正文→参数渲染→根据 context 决定 inline 或 fork。`ActiveSkillManager`：`record()` 记录激活→`build_context()` 紧凑后重挂→`disallowed_tools()` 聚合。

**`prompt.py`**：`build_skill_descriptions()` 生成"Available skills"列表——每个 skill 一行 metadata（名称+描述+调用方式）。

## 5. 设计决策

### 为什么正文懒加载

发现阶段只读 frontmatter——回答"有哪些 skill"。调用时才付出正文读取成本。一次性加载全部正文浪费 token。

### 为什么 fork skill 需要独立 Agent

inline：中间结果留在主上下文。fork：隔离消息历史，只返回最终结果——等同于轻量子 Agent。

### 为什么项目级覆盖用户级

项目可能有特殊流程（部署到特定平台），覆盖用户的通用模板。优先级：项目 > 用户。

## 6. 面试考点

**Q: 为什么三层披露？** 上下文窗口有限。metadata 够模型判断该不该用，正文在决定时加载。

**Q: 和子 Agent 什么关系？** fork 模式的 skill 就是子 Agent——通过 `agent.run_once()` 执行。inline 模式则注入主对话。

## 7. 代码导读

**关键行号**：`registry.py` SkillRegistry._load_skills_from_dir()、`runtime.py` SkillInvocation.invoke() + render_prompt()、`runtime.py` ActiveSkillManager.record() + build_context()。
