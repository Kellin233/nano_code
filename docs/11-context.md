# 上下文管理

## 1. 为什么需要上下文管理

模型每次 API 调用前，系统组装"输入包"——system prompt + CLAUDE.md + Git 状态 + skill 列表 + 消息历史。这看起来简单（拼字符串），但有两个真实挑战。

**挑战一**：什么该放进稳定 system prompt（影响 Anthropic prompt cache 命中率），什么该作为动态附件注入？改一个字就可能让整个 system prompt 缓存 miss。

**挑战二**：对话太长时怎么压缩消息历史而不丢关键信息？超过 200K token 窗口就报错——但 compact 本身是一次模型调用（消耗 token）。

上下文管理解决的就是这两个问题。

## 2. 核心概念

### 2.1 稳定 vs 动态分离

```
STABLE_SYSTEM_PROMPT（固定模板）
    ├── System：角色定义
    ├── Doing tasks：行为规范
    ├── Using your tools：工具使用指南
    └── Output efficiency：效率要求
─────────────────────────────────
__NANO_CODE_SYSTEM_PROMPT_DYNAMIC_BOUNDARY__
─────────────────────────────────
启动上下文（仅首次）：日期 + 平台 + Shell + CLAUDE.md + Git
动态附件（按需）：Skill 列表、Deferred Tools、MCP Delta
记忆注入（每次）：LLM 精选的最相关记忆 + freshness warning
```

所有动态内容通过 `append_user_context()` 以 user message 形式注入——不改 system prompt。系统的 "stable boundary" 之后的变化不影响 cache 命中。

### 2.2 CLAUDE.md 加载链

优先级从低到高：`~/.claude/CLAUDE.md`（用户全局）→各目录 `CLAUDE.md`→`.claude/CLAUDE.md`→`.claude/rules/*.md`→`CLAUDE.local.md`（本地覆盖）。支持 `@path/to/file.md` include 语法，深度 5 层，总预算 60K 字符，单文件 20K。HTML 注释剥离（代码块内保留）。

### 2.3 Git 快照

启动时用 `ThreadPoolExecutor` 并行 5 个 git 命令（branch、remote_head、status、log、user），3s 超时。一次性快照——不随对话更新。为什么？对话中代码不断变化，实时更新导致消息历史中出现多个矛盾版本。快照标注"对话开始时拍的"。

## 3. 总体设计

```
context/
├── builder.py    # 稳定 system prompt + 启动上下文 + 5 个 render_* 附件函数
└── sources.py    # CLAUDE.md 加载 + Git 快照 + frontmatter 解析
                  # 含共享类型（PromptDiagnostic、PromptBundle）——避免循环导入
```

## 4. 详细设计

**`builder.py`**：`STABLE_SYSTEM_PROMPT` 字符串常量——四个 section，约 120 行。`build_startup_context()` 生成首次注入的 `<system-reminder>` 块。5 个 `render_*` 函数：skill_listing（只列 metadata，不注入正文）、deferred_tools（可被 tool_search 激活的工具名列表）、mcp_delta（added/changed/removed 通知）、memory_attachment、system_reminder（通用包裹函数）。

**`sources.py`**：`load_project_instructions()` 扫描 CLAUDE.md 文件链——按优先级排序、HTML 剥离、include 解析、总预算限制。`collect_git_context()` 并行 git 命令。`parse_frontmatter()` 解析 `---` 分隔的 YAML 元数据——被 memory、skills、subagents、CLAUDE.md loader 共用。

**共享类型**：`PromptDiagnostic`（info/warning/error）、`PromptBundle`（system_prompt+startup_context+diagnostics）、`ContextAttachment`、`FrontmatterResult`。放在 `sources.py` 而非 `builder.py`——因为 `builder.py` 需要 import `sources.py`（调用 load_project_instructions、collect_git_context），反过来 sources 不需要 import builder。避免循环导入。

## 5. 设计决策

### 为什么稳定提示词和动态附件分离

Anthropic 的 prompt caching 基于前缀匹配。只要 system prompt 不变，cache 就命中。所有动态内容放 `DYNAMIC_BOUNDARY` 之后，随意改不影响 cache。

### 为什么 Git 是一次性快照

对话中代码被不断修改——多个 Git status 版本会互相矛盾。一次性快照标注"对话开始时拍的"。

### 为什么共享类型放 sources.py

`builder → sources` 是单向依赖（builder 调 sources 的函数）。如果把类型放 builder，sources 需要反向 import builder——循环。放 sources 打破循环。

## 6. 面试考点

**Q: 改什么内容不会让 Anthropic prompt cache 失效？** `DYNAMIC_BOUNDARY` 之后的变化都不影响。改 CLAUDE.md、调整附件时机——都安全。改 `STABLE_SYSTEM_PROMPT` 任何文字都 miss。

**Q: CLAUDE.md include 怎么防止无限递归？** 深度限制 5 层。`stack: list[Path]` 追踪当前 include 链——检测到循环就跳过并记录 diagnostic。

**Q: 为什么共享类型放 sources.py？** 避免 builder→sources 循环导入。builder 需要 sources 的函数（load_project_instructions、collect_git_context），sources 不需要 builder。类型放 sources 打破可能的循环。

## 7. 代码导读

**关键行号**：`builder.py` STABLE_SYSTEM_PROMPT 常量、`builder.py` build_startup_context()、`sources.py` load_project_instructions() 文件发现链、`sources.py` collect_git_context() ThreadPoolExecutor 并行。
