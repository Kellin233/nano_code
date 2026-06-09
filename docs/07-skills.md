# 技能系统

## 为什么需要 Skills

有些任务需要重复的提示词模板——"写 git commit"、"做代码审查"。如果让用户每次都手打一段长 prompt，效率低且不一致。Skills 把常用的提示词模板化，用户或模型调用一个名字就能触发。

Skills 不是"插件"——它们不引入新代码，只是提示词模板。安全边界仍然由工具白名单和权限系统控制。

## 核心概念

### 三层阶段式披露

这是整个系统的核心设计——只在需要时给模型刚好够用的上下文：

| 层 | 时机 | 内容 | token 成本 |
|:--:|------|------|:--:|
| 1 | 会话启动 | 名称、一句话描述、调用方式 | 每 skill 一行 |
| 2 | 调用时 | 完整 SKILL.md body + 参数渲染 | 按需 |
| 3 | 模型主动读 | supporting files（skill 目录下其他文件） | 按需 |

为什么不一次性注入？一个项目可能有几十个 skill——全部注入浪费 token。发现阶段只读 frontmatter（metadata），正文在调用时懒加载。

### inline vs fork

| 模式 | 行为 | 适用 |
|------|------|------|
| inline（默认） | 渲染后提示词注入当前对话 | 轻量任务（写 commit message） |
| fork | 创建独立子 Agent | 重量任务（代码审查），不污染主上下文 |

### 参数替换

`$ARGUMENTS`、`$0`、`${CLAUDE_SKILL_DIR}` 在调用时被替换为实际值。如果传了参数但正文没用占位符，参数追加到正文末尾 `ARGUMENTS:` 区块。

### Active Skill 管理

compact 压缩后，已激活的 skill 指令会丢失。`ActiveSkillManager` 在 compact 后通过 `build_context()` 重新注入——最多 8 个 active skill，每个最多 5000 token。

## 设计决策

### 为什么正文懒加载而非一次性加载

发现阶段只读 frontmatter（metadata），不读 SKILL.md 正文。发现阶段回答"有哪些 skill 可用"（token 极低），调用阶段才付出正文读取和上下文成本。如果一次性加载所有 skill 正文，几十个 skill 可能占掉大半个上下文窗口。

### 为什么 fork skill 需要独立 Agent

inline 模式下，skill 的提示词注入主 Agent 的对话——skill 产生的中间搜索结果、错误信息都会留在主上下文里。fork 模式给 skill 独立的消息历史，skill 只把最终结果带回。这等同于轻量级的子 Agent。

### 为什么项目级覆盖用户级

`.claude/skills/deploy/SKILL.md`（项目级）覆盖 `~/.claude/skills/deploy/SKILL.md`（用户级）。项目可能有特殊的部署流程，覆盖用户的通用模板。优先级链：项目 > 用户。

## 代码走读

**`types.py`**：`SkillDefinition`（skill 元数据）+ `SkillInvocationResult`（调用结果）+ `ActiveSkill`（激活状态）。

**`registry.py`**：`SkillRegistry` 扫描 `.claude/skills/`。`get(name)` 查找，项目覆盖用户。`discover_skills()` 全量列出。

**`runtime.py`**：`SkillInvocation.invoke()` 权限检查 + 懒加载 + 参数渲染。`ActiveSkillManager` 记录激活、compact 重挂、聚合 disallowed_tools。

**`prompt.py`**：`build_skill_descriptions()` 生成 system prompt 中的 skill 列表片段。

## 面试考点

**Q: 为什么三层披露而不一次性加载？**

上下文窗口有限。几十个 skill 的全文会是巨大的 token 浪费。metadata 足够让模型判断"该不该用这个 skill"——正文在决定使用时才加载。
