# 技能系统

## 1. 为什么需要 Skills

"写 git commit"、"做代码审查"——这些任务每次都需要一段特定的提示词。Skills 把常用的提示词模板化，用户或模型调用名字就触发。不是"插件"——它们不引入新代码，只是提示词模板。

## 2. 核心概念

### 2.1 三层阶段式披露

只在需要时给模型刚好够用的上下文：

| 层 | 时机 | 内容 | cost |
|:--:|------|------|:--:|
| 1 | 会话启动 | 名称、描述、调用方式 | 每 skill 一行 |
| 2 | 调用时 | 完整 SKILL.md body + 参数 | 按需 |
| 3 | 模型主动读 | supporting files | 按需 |

发现阶段只读 frontmatter 不读正文。为什么？一个项目几十个 skill——全部加载浪费 token。metadata 足够让模型判断"该不该用"。

### 2.2 inline vs fork

inline（默认）：渲染后注入当前对话。fork：创建独立子 Agent 执行，不污染主上下文。

### 2.3 Active Skill 管理

compact 压缩后 skill 指令丢失——`ActiveSkillManager.build_context()` 重新注入。最多 8 个 active skill，每 skill 最多 5000 token。

## 3. 总体设计

```
capabilities/skills/
├── types.py      # SkillDefinition、ActiveSkill、SkillInvocationResult
├── registry.py   # SkillRegistry：发现、缓存、查找
├── runtime.py    # SkillInvocation + ActiveSkillManager（invocation+active 合并）
└── prompt.py     # 提示词描述渲染
```

## 4. 详细设计

**`registry.py`**：`SkillRegistry` 扫描 `.claude/skills/`。项目级覆盖用户级同名 skill。发现阶段只读 frontmatter——不读正文（懒加载）。`get_skill_by_name()` 快捷入口。

**`runtime.py`**：`SkillInvocation.invoke()` 权限检查（user-invocable/disable-model-invocation）+ 懒加载 + 参数替换（`$ARGUMENTS`/`$0`/`${CLAUDE_SKILL_DIR}`）。`ActiveSkillManager` 记录激活、compact 重挂、聚合 disallowed_tools。

## 5. 设计决策

### 为什么正文懒加载

发现阶段只读 frontmatter——只回答"有哪些 skill"。调用时才付出正文读取成本。如果一次性加载所有正文，token 浪费严重。

### 为什么 fork skill 需要独立 Agent

inline 模式：skill 的中间结果留在主上下文。fork 模式：独立消息历史，只返回最终结果。等同于轻量子 Agent。

## 6. 面试考点

**Q: 为什么三层披露？** 上下文窗口有限。metadata 够模型判断该不该用，正文在决定时加载。

## 7. 代码导读

**关键代码**：`registry.py` SkillRegistry._load_skills_from_dir()、`runtime.py` SkillInvocation.invoke() + ActiveSkillManager.build_context()。
