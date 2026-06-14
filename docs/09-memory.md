# 记忆系统

## 1. 为什么需要记忆

LLM 对话本身只保存当前 session 的消息历史。跨会话仍然有一些信息值得保留，例如用户的代码风格偏好、项目外部约束、稳定的调试坑。NanoCode 的记忆系统把这类信息保存成用户可编辑的 Markdown 文件，并在新会话启动时作为系统上下文注入。

当前实现刻意保持轻量：

- 不做向量库。
- 不做 semantic recall。
- 不做 LLM side-query 精选。
- 不做后台抽取或自动长期晋升。
- 不保存文件摘要、最近读过的文件或短期任务状态。

记忆是应用层能力，位于 `cli/core/memory/`。Agent core 不知道 memory 存在；`AgentSession` 创建 `MemoryRuntime`，并把 memory 规则加入 system prompt，把当前项目 memory 加入 startup context。

## 2. 文件结构

```
cli/core/project/
└── identity.py   # ProjectScope、项目身份、project key、项目级数据目录

cli/core/memory/
├── __init__.py
├── paths.py      # memory 路径兼容转发层
├── types.py      # topic 常量、预算、MemoryTopic
├── store.py      # Markdown topic 读写、MEMORY.md 索引同步
└── runtime.py    # MemoryRuntime：system prompt 规则、startup context、/remember
```

模块边界：

| 文件 | 职责 |
|------|------|
| `cli/core/project/identity.py` | 根据 Git common dir 或 cwd 生成稳定 project key，并返回项目级数据目录 |
| `paths.py` | 保留 memory 对外 helper，内部转发到统一 ProjectScope 逻辑 |
| `types.py` | 定义固定 topic、别名、描述和大小预算 |
| `store.py` | 管理 topic Markdown 文件和 `MEMORY.md` 索引 |
| `runtime.py` | 注入 memory 使用规则，构建启动上下文，执行显式保存 |

## 3. 存储模型

记忆按项目隔离，默认存放在用户目录：

```
~/.nanocode/projects/<repo_key>/memory/
├── MEMORY.md        # 索引，只列出已有 topic 文件
├── preferences.md   # 用户偏好和行为反馈，按需创建
├── project.md       # 项目决策、目标、外部约束，按需创建
└── debugging.md     # 稳定环境、工具、测试坑，按需创建
```

`repo_key` 来自统一的 `ProjectScope`：

1. 如果在 Git 仓库中，使用 `git rev-parse --git-common-dir` 的结果作为身份。
2. 否则使用当前 workspace 的绝对路径。
3. 最终目录名是安全项目名 + 路径 SHA-256 前 16 位。

这样同一个 Git worktree 下不同子目录会共享 project memory；不同项目即使命名相同，也会因路径 hash 不同而隔离。

项目身份只允许在 `cli/core/project/identity.py` 中计算。memory runtime、`/memory path`、测试 fixture 和后续项目级数据目录都应复用同一套 `ProjectScope`，避免写入和读取使用不同 repo key。

`ProjectScope` 不是 memory 专属类型，而是“项目级用户数据”的统一身份层：

| 字段 | 含义 | 设计作用 |
|------|------|----------|
| `workspace` | 当前运行目录的绝对路径 | 保留本次 session 的实际工作入口 |
| `identity_path` | Git common dir 或非 Git workspace | 决定哪些目录共享同一份项目级数据 |
| `project_key` | 安全名称 + 路径 hash | 既可读，又避免同名项目冲突 |
| `project_dir` | `~/.nanocode/projects/<repo_key>` | memory、后续项目级缓存或状态的父目录 |

这个设计避免把“当前 cwd”误当成项目身份。对于 monorepo 子目录或 Git worktree，用户在不同子目录启动 NanoCode 时通常希望共享同一组长期项目约束；对于两个路径不同但目录名相同的项目，hash 又能保证隔离。

## 4. Topic 设计

当前只保留三个 topic：

| Topic | 文件 | 保存什么 | 不保存什么 |
|-------|------|----------|------------|
| `preferences` | `preferences.md` | 用户偏好、风格要求、行为反馈 | 本轮临时要求、代码事实 |
| `project` | `project.md` | 项目决策、目标、外部约束、不能从代码推导的引用 | 目录结构、函数说明、近期改动 |
| `debugging` | `debugging.md` | 稳定环境坑、测试命令坑、工具链问题 | 普通 debug 步骤、已修复的一次性错误 |

支持别名，例如 `debug` → `debugging`、`prefs` → `preferences`、`proj` → `project`。

减少 topic 数量是有意设计：topic 太多会让模型和用户都难以判断该存哪里，也会制造重复文件。当前三类足够覆盖跨会话有价值但不能从代码实时推导的信息。

topic 模型遵循一个原则：topic 文件是事实正文，`MEMORY.md` 只是可再生目录。模型读取 memory 时会先看到索引，随后按固定顺序读取 topic 正文；用户或工具编辑 topic 后，索引可以从当前 topic 文件重新生成。因此不要把稳定事实只写进 `MEMORY.md`，否则下一次同步索引时会丢失。

固定 topic 也减少了权限和上下文治理复杂度。`write_file`/`edit_file` 只需要识别 `MEMORY.md` 和三个 topic 文件，就能在写入后触发索引同步；如果允许任意 topic，需要额外解决命名、预算、索引描述、别名冲突和误保存临时文件的问题。

## 5. MEMORY.md 的角色

`MEMORY.md` 只维护索引，不保存正文事实：

```markdown
# Memory Index

- [preferences.md](preferences.md): User preferences and behavior feedback.
- [project.md](project.md): Project decisions, goals, external constraints, and references not derivable from code.
```

索引由 `store.update_memory_index()` 自动生成：

- `/remember` 写入 topic 后刷新索引。
- 工具直接写入或编辑 memory topic 文件后刷新索引。
- 空 memory 目录下索引会显示 `No local memories saved yet.`。

把正文放在 topic 文件而不是 `MEMORY.md`，可以避免一个大索引文件同时承担“目录”和“事实库”两种职责。

## 6. 启动注入

`MemoryRuntime.apply_to_system_prompt()` 会把 Local Memory 规则插入 stable system prompt 的动态边界前。规则强调：

- memory 是 point-in-time context，不是 live project state。
- 代码行为、架构事实、文件路径应优先读当前文件验证。
- 不要把源码结构、git 历史、最近编辑、普通调试步骤保存为 memory。
- 用户明确要求忽略 memory 时，本轮视为不可用。
- 保存相对日期前要转成绝对日期。

`MemoryRuntime.build_startup_context()` 在会话启动时读取已有 topic：

1. 列出 memory 目录。
2. 加载 `MEMORY.md` 索引。
3. 按 `preferences`、`project`、`debugging` 顺序读取非空 topic。
4. 每个 topic 最多 16KB，总 memory 上下文最多 40KB。
5. 超预算时跳过后续 topic，并在上下文中说明跳过原因。
6. 如果 topic 修改时间超过 1 天，会提示模型验证代码相关说法。

这些内容通过 `<system-reminder>` 注入 startup context，不作为独立检索结果反复追加。

启动注入分成两层：

- stable system prompt 中插入 memory 使用规则，告诉模型“如何对待 memory”。
- startup context 中插入当前项目已有 memory，告诉模型“本项目当前保存了什么”。

这两个层次不能混在一起。规则需要长期稳定，适合放进 system prompt；topic 正文是项目状态快照，可能过期、可能超预算、也可能被用户要求忽略，适合作为 `<system-reminder>` 进入当前会话。compact 后的恢复也调用 `build_compact_context()`，本质上是重新读取当前磁盘上的 topic，而不是从旧 conversation 中拷贝一份可能过时的 memory。

## 7. 显式写入

用户通过 REPL 命令写入：

```text
/remember preferences 代码风格要简洁务实，不要炫技。
/remember project 2026-06-11: checkpoint/resume 采用 session.jsonl 作为事实来源。
/remember debugging 本仓库测试建议用 PYTHONPATH=src python -m unittest discover -s test。
```

写入流程：

```
TUI command
  → RuntimeThread.remember_memory(topic, text)
  → AgentSession.remember_memory(topic, text)
  → MemoryRuntime.remember(topic, text)
  → store.append_memory(topic, text)
  → write_text_atomic(topic.md)
  → update_memory_index()
```

每次写入会追加一个日期小节：

```markdown
## 2026-06-11

- 代码风格要简洁务实，不要炫技。
```

当前没有自动晋升策略。模型如果认为某条信息值得长期保存，应明确建议用户使用 `/remember`，或在用户明确要求时执行对应命令。

显式写入的好处是责任清楚：用户知道哪些内容会跨会话保存，review 时也能直接打开 Markdown 文件确认。自动从 final answer、工具输出或 debug 过程抽取 memory 会制造两个问题：一是容易把临时事实保存成长期约束，二是用户很难知道为什么后续会话被某条旧信息影响。

## 8. 工具写入与索引同步

`write_file` 和 `edit_file` 在写入成功后会调用 `sync_memory_file(path)`。如果目标路径正好是当前项目 memory 目录下的 `MEMORY.md` 或三个 topic 文件之一，就会自动刷新索引。

这样用户或模型可以直接编辑 topic 文件，同时保持 `MEMORY.md` 不漂移。

同步范围刻意很窄：只有当前项目 memory 目录下的索引和三个固定 topic 会触发刷新。普通项目文件、workspace 外文件、任意用户目录下的 Markdown 都不会被当成 memory。这让文件工具仍保持通用，不需要理解 memory 语义，也避免“写了一个同名文件就触发长期记忆”的隐式行为。

## 9. 与项目指令的边界

项目共享规则来自：

```
AGENTS.md
.nanocode/rules/*.md
```

Local memory 是用户私有的跨会话辅助上下文。两者边界不同：

| 类型 | 位置 | 适合内容 | 优先级 |
|------|------|----------|--------|
| Project instructions | 仓库内 | 团队共享规则、项目约定、长期开发规范 | 高 |
| Local memory | 用户目录 | 用户偏好、个人经验、外部约束、稳定环境坑 | 低于当前文件和项目指令 |

如果 memory 和当前文件、Git 状态、项目指令冲突，应以当前文件和项目指令为准。

## 10. 边界与失败模式

Memory 系统的失败策略偏向可见、可恢复：

| 场景 | 行为 | 原因 |
|------|------|------|
| unknown topic | `/remember` 报错并列出合法 topic | 防止拼写错误生成长期孤儿文件 |
| 空文本 | 拒绝写入 | 空 entry 没有长期价值，还会污染索引 |
| topic 超过 16KB | 启动上下文只加载前段并标记 truncated | 保持 prompt 预算可控，正文仍完整保存在磁盘 |
| 总 memory 超过 40KB | 跳过后续 topic，并在上下文中说明 | 不让 memory 挤占当前任务上下文 |
| topic 修改时间超过 1 天 | 注入“代码相关说法需验证”提醒 | memory 是 point-in-time context，不是实时事实 |
| 直接编辑 memory topic | 写入成功后同步 `MEMORY.md` | 允许手工维护，同时保持索引可再生 |
| memory disabled | 不改 system prompt，也不注入 startup context | 让测试或特殊运行模式可以明确关闭该能力 |

这些边界也解释了为什么 memory 不做“自动纠错”。如果旧 memory 和代码冲突，正确行为是读当前文件并以当前文件为准，而不是由 memory runtime 猜测哪一方更新。

## 11. 不做什么

当前 memory 第一版明确不做：

- 文件摘要缓存。
- ReadFileTracker 或 read ledger。
- 短期记忆窗口。
- 自动从 assistant final answer 抽取长期记忆。
- LLM side-query 精选。
- 向量检索或 embedding。
- 后台维护、衰减、归档。

这些不是永远不能做，而是当前目标更需要稳定、可审计、少文件、少隐式行为的记忆机制。几个重要取舍是：

- 不做向量库：当前 topic 数量少、内容短，顺序读取更透明；embedding 召回会让“为什么这条记忆出现”变得难审计。
- 不做文件事实缓存：代码、目录和测试结果可以从当前 workspace 读取；缓存它们只会制造过期事实和冲突优先级问题。
- 不做自动长期晋升：长期记忆需要用户意图或明确命令，不能由模型单方面把一次任务经验升级为跨会话规则。
- 不做 LLM side-query：额外模型调用会增加成本和不确定性，而且会把 memory 变成一个独立推理系统。

需要扩展时应先证明真实场景需要，再保持 `MemoryRuntime` 不污染 Agent core。新增能力也应继续遵守“用户可编辑 Markdown 是事实来源”的原则。

## 12. Benchmark 覆盖

`benchmarks/local-fixture` 的 memory 任务验证三条设计约束：

- `memory_fact_lookup`：项目 memory 会在启动时注入，模型可直接使用稳定外部事实。
- `memory_edit_dependency`：memory 可以提供编辑依赖值，但仍通过普通文件工具完成修改。
- `memory_irrelevant_guard`：memory 与当前文件事实冲突时，应读取当前文件并以当前文件为准。

这些任务也约束 memory 不应退化成“读过文件的缓存”。当前文件、项目指令和用户本轮要求的优先级高于历史 memory。

维护者可以用这些问题自查：

- 为什么同一个 Git 仓库不同子目录共享 memory，而两个同名目录不共享？
- `MEMORY.md` 为什么不能保存正文事实？
- compact 后 memory 为什么重新从磁盘读，而不是沿用摘要前的旧上下文？
- 如果 memory 说测试命令是 A，但当前文件或用户说是 B，Agent 应该先验证哪一边？

## 13. 代码导读

```
cli/core/project/identity.py
cli/core/memory/paths.py
cli/core/memory/types.py
cli/core/memory/store.py
cli/core/memory/runtime.py
cli/session.py::remember_memory
cli/session.py::memory_summary
tui/commands.py
```
