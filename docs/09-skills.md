# Skills 系统设计

## 目标

Skills 是 Claude Code 风格的"可复用提示词模板"系统。用户或模型调用一个 skill 名字，系统返回渲染后的提示词。设计核心是三层阶段式披露：只在使用时才加载完整内容。

## 代码流程

```
Skill 调用路径：
    /skill-name (用户 REPL) → TuiApp._try_skill()
    或 skill tool (模型调用) → ToolRegistry._call_builtin("skill", inp)
         │
         ▼
    SkillInvocation.invoke(skill_name, args, invoked_by)
         │
         ├── SkillRegistry.get(name)  → 查找 SkillDefinition
         │     ├── 优先项目级 .claude/skills/<name>/SKILL.md
         │     └── fallback 用户级 ~/.claude/skills/<name>/SKILL.md
         │
         ├── 权限检查：user-invocable / disable-model-invocation
         ├── 懒加载：skill.path 存在就读文件 + parse_frontmatter
         │
         ├── render_prompt()  → 参数替换（$ARGUMENTS, $0, ${CLAUDE_SKILL_DIR}）
         │
         └── 根据 context 决定行为：
               context=inline  → 注入提示词到当前对话
               context=fork   → 创建子 Agent 独立执行
```

## 总体设计

### 文件结构

```
capabilities/skills/
├── __init__.py       # 公共导出
├── types.py          # SkillDefinition、ActiveSkill、SkillInvocationResult
├── registry.py       # SkillRegistry：发现、缓存、查找
├── runtime.py        # SkillInvocation + ActiveSkillManager（合并 invocation+active）
└── prompt.py         # 提示词渲染辅助函数
```

### 模块职责

| 模块 | 职责 | 变更原因 |
|------|------|---------|
| `types.py` | 数据结构 | 改 skill 的 frontmatter 字段时改 |
| `registry.py` | 发现与查找 | 改扫描路径/缓存策略时改 |
| `runtime.py` | 调用执行 + 激活状态管理 | 改调用逻辑/active 状态时改 |
| `prompt.py` | 提示词描述生成 | 改 system prompt 中 skill 元数据格式时改 |

### 三层阶段式披露

这是整个系统的核心设计理念——只在需要时给模型刚好够用的上下文：

| 层 | 时机 | 内容 | token 成本 |
|:--:|------|------|:--:|
| 第一层 | 会话启动 | skill 名称、简短描述、调用方式 | 极低（每 skill 一行） |
| 第二层 | 用户/模型调用 | 完整 SKILL.md body + 参数渲染 | 中等（按需加载） |
| 第三层 | 模型主动读取 | supporting files（skill 目录下其他文件） | 按需（模型用 read_file） |

**为什么不一次性注入所有 skill 内容**：context 窗口是有限的。一个项目可能有几十个 skill，全部注入会严重浪费 token。

### 调用模式：inline vs fork

| 模式 | context 字段 | 行为 |
|------|-------------|------|
| inline | `context: inline`（默认） | 渲染后的提示词注入当前对话，主 Agent 自己执行 |
| fork | `context: fork` | 创建独立子 Agent（`is_sub_agent=True`），在隔离上下文中执行 |

fork 模式适用场景：skill 涉及大量独立工作（代码审查、批量重构），不应污染主 Agent 的上下文。

### Active Skill 管理

`ActiveSkillManager` 负责记录当前会话中已激活的 skill。compact 压缩后，通过 `build_context()` 重新注入——保证模型不会因为 compact 丢失 skill 指令。限制最多 8 个 active skill，每 skill 最多 5000 token，总计 25000 token。

## 详细设计

### `registry.py`——Skill 发现

`SkillRegistry` 负责扫描 `.claude/skills/` 目录。项目级 skill 覆盖同名的用户级 skill。发现阶段只读 frontmatter——不读 SKILL.md 正文（懒加载）。

frontmatter 字段：

```yaml
---
name: code-review
description: 审查代码变更
context: fork
agent: explore
allowed-tools: read_file, grep_search, list_files
disallowed-tools: write_file, run_shell
user-invocable: true
disable-model-invocation: false
argument-hint: "branch name"
---
```

`get_skill_by_name(name)` 是单 skill 查找的快捷入口。`discover_skills()` 返回所有已发现的 skill。

### `runtime.py`——Skill 运行时

`SkillInvocation.invoke(name, args, invoked_by)` 是调用入口：
- 权限检查：`user-invocable` / `disable_model-invocation` 按调用者类型判断
- 懒加载：通过 `skill.path` 读取完整 SKILL.md，`parse_frontmatter` 提取 body
- 参数渲染：替换 `$ARGUMENTS`、`$0`（位置参数）、`${CLAUDE_SKILL_DIR}`（skill 目录路径）

`ActiveSkillManager` 维护活跃 skill 列表：
- `record(invocation)` 记录成功调用
- `disallowed_tools()` 聚合所有活跃 skill 的禁用工具
- `build_context()` 生成 compact 后重挂的上下文
- `clear()` 清空

### `prompt.py`——提示词生成

辅助函数用于 system prompt：
- `build_skill_descriptions()`：生成 skill 列表文字（供 Agent 初始化附件使用）
- `resolve_skill_prompt()`：渲染单个 skill 的提示词（供兼容调用方）
- `execute_skill()`：旧调用路径的兼容入口

## 硬性约束

- 三层披露不可跳过：启动时只加载 metadata，不加载正文
- fork skill 的子 Agent 不给 agent 工具（防止递归）
- active skill 的 disallowed_tools 必须在 `ToolRegistry.active_definitions()` 中生效

## 隐含要求

- 项目级 skill 覆盖用户级同名 skill
- `SkillRegistry` 的 `reset_skill_cache()` 在测试中清理缓存
- lazy body loading 不是带缓存的一次性懒加载——每次调用都重新读取文件，确保正文总是最新

## 不能做什么

- 不能把 skill 正文注入 system prompt（必须按需加载）
- 不能在 fork skill 中让子 Agent 继续 fork 子 Agent
- 不能把 supporting files 自动注入上下文

## 可能踩坑的地方

### frontmatter 字段命名不一致

代码同时支持 `user-invocable` 和 `user_invocable`（下划线/连字符）。写 SKILL.md 时两种都能用，但 `user-invocable` 是推荐格式，与 Claude Code 保持一致。

### active skill 的 token 预算

`per_skill_token_budget = 5000`，`total_token_budget = 25000`。如果 skill 正文很长，`_truncate_to_tokens` 会截断。截断后的上下文可能丢失关键指令——skill 作者应把关键指令放在 skill 正文开头。

### fork skill 的 agent type

frontmatter 可指定 `agent: explore` 来限制子 Agent 类型。如果指定的类型不存在（如自定义子 Agent 被删除），会回退到 `general` 并附加一条提示。
