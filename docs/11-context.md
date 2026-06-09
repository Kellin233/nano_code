# 上下文管理

## 1. 为什么需要上下文管理

模型每次 API 调用前，系统组装"输入包"——system prompt + 项目指令 + git 状态 + skill 列表 + 消息历史。这有两个挑战：(1) 什么该放进 stable system prompt（影响 Anthropic prompt cache 命中率），(2) 对话太长时怎么压缩不丢关键信息。

## 2. 核心概念

### 2.1 稳定 vs 动态分离

```
STABLE_SYSTEM_PROMPT（固定模板）→ 利于 prompt caching
─────────────────────────────
启动上下文（日期/CLAUDE.md/Git）→ 首次注入
动态附件（Skill/Deferred Tools/MCP）→ 按需注入
记忆召回 → 每次用户回合
```

动态内容通过 `append_user_context()` 以 user message 注入——不改 system prompt。system prompt 的稳定性 = cache 命中率。

### 2.2 CLAUDE.md 加载链

`~/.claude/CLAUDE.md` → 各级 `CLAUDE.md` → `.claude/CLAUDE.md` → `.claude/rules/*.md` → `CLAUDE.local.md`。支持 `@path/to/file.md` include，深度 5 层，总预算 60K。

## 3. 总体设计

```
context/
├── builder.py    # System prompt + 启动上下文 + 5 个 render_* 附件函数
└── sources.py    # CLAUDE.md 加载 + Git 快照 + frontmatter（含共享类型）
```

类型定义（PromptDiagnostic、PromptBundle）放在 sources.py——避免 builder↔sources 循环导入。

## 4. 详细设计

**`builder.py`**：`STABLE_SYSTEM_PROMPT` 固定模板（System/Doing tasks/Using tools/Output efficiency 四个 section）。`build_startup_context()` 生成启动上下文（日期/平台/Shell/Git/CLAUME.md）。5 个 render 函数：skill_listing、deferred_tools、mcp_delta、memory_attachment、system_reminder。

**`sources.py`**：`load_project_instructions()` 扫描 CLAUDE.md 链，HTML 注释剥离（代码块内保留），include 解析。`collect_git_context()` 用 ThreadPoolExecutor 并行 5 个 git 命令，3s 超时。

## 5. 设计决策

### 为什么 Git 是一次性快照

对话中代码被不断修改——实时更新会导致多个版本矛盾。一次性快照标注"对话开始时拍的"。

### 为什么类型放 sources.py

PromptDiagnostic 被 builder 和 sources 同时需要。放 sources.py 因为 sources 不需要 import builder——打破循环。目录级循环已消除为单向依赖。

## 6. 面试考点

**Q: 改什么不会让 Anthropic prompt cache 失效？** `DYNAMIC_BOUNDARY` 之后的变化都不影响。改 CLAUDE.md、调整附件时机——都安全。改 `STABLE_SYSTEM_PROMPT` 任何文字都 miss。

## 7. 代码导读

**关键代码**：`builder.py` STABLE_SYSTEM_PROMPT + build_startup_context()、`sources.py` load_project_instructions() + collect_git_context()。
