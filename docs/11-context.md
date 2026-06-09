# 上下文管理

## 为什么需要上下文管理

模型每次 API 调用时，系统需要组装一份"输入包"——system prompt + project instructions + git status + skill list + 消息历史。这件事看起来简单（拼字符串），但有两个真实挑战：

1. **什么该放进 system prompt，什么该放进动态附件？** system prompt 影响 Anthropic 的 prompt cache 命中率——改一个字缓存就 miss。
2. **对话太长了怎么办？** 消息历史超过了上下文窗口，必须压缩但不丢关键信息。

上下文管理解决的就是这两个问题。

## 核心概念

### 稳定 vs 动态分离

```
STABLE_SYSTEM_PROMPT（固定模板）     ← 利于 Anthropic prompt caching
─────────────────────────────
DYNAMIC_BOUNDARY 分隔标记
─────────────────────────────
启动上下文（日期/CLAUDE.md/Git）     ← 首次注入
动态附件（Skill/Deferred Tools/MCP）  ← 按需注入
记忆召回结果                         ← 每次用户回合
```

所有动态内容通过 `append_user_context()` 以 user message 形式注入——不改 system prompt。system prompt 的稳定性 = cache 命中率。

### CLAUDE.md 加载链

```
~/.claude/CLAUDE.md          # 用户全局（最低优先级）
  → 各级目录 CLAUDE.md        # 项目
  → .claude/CLAUDE.md         # 项目配置
  → .claude/rules/*.md        # 按路径匹配的规则
  → CLAUDE.local.md           # 本地覆盖（最高优先级）
```

支持 `@path/to/file.md` include 语法，最大深度 5 层，总预算 60K 字符。

## 设计决策

### 为什么 Git 上下文是一次性快照

对话中代码被 Agent 不断修改——如果实时更新 Git status，消息历史里会出现多个版本的 status 互相矛盾。一次性快照明确标注"这是对话开始时拍的"。

### 为什么三层压缩有不同的触发阈值

阈值递增（50% → 60% → 85%），从温和到激进。每层的成本递增：Budget 是纯字符串操作（零 API 成本），Snip 需要先扫全量消息建索引（O(n) 内存），Compact 是一次模型调用（消耗 token）。只在必要时才付出更高的成本。

## 代码走读

**`builder.py`**：`STABLE_SYSTEM_PROMPT` 固定模板 + `build_startup_context()` + 5 个 `render_*()` 附件渲染函数。

**`sources.py`**：`load_project_instructions()` CLAUDE.md 链加载、`collect_git_context()` 并行 5 个 git 命令、`parse_frontmatter()` YAML 解析。类型定义（`PromptDiagnostic`、`PromptBundle`）也在此文件——避免 builder.py 和 sources.py 循环导入。

## 面试考点

**Q: 改什么内容不会让 Anthropic prompt cache 失效？**

任何在 `DYNAMIC_BOUNDARY` 之后的变化都不影响缓存。所以改 CLAUDE.md 内容、调整附件注入时机——都安全。但改 `STABLE_SYSTEM_PROMPT` 任何文字都会 miss。

**Q: compact 失败怎么办？**

降级——保留未压缩的历史继续对话，不中断会话。compact 本身是一次模型调用（`max_tokens=2048`），可能因 API 限流或网络问题失败。
