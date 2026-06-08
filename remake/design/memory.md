# Memory 重构方案

## 目标

把 `nanocode` 的长期记忆系统升级为一个低依赖、可审计、可维护的文件式记忆模块。

本方案采用前面讨论的 **方案 B+**：

```text
文件式记忆
+ 结构化元数据
+ 来源和证据字段
+ 多视角候选召回
+ LLM side-query 精选
+ 预算打包和 freshness warning
+ 显式维护：去重、衰减、归档
```

这里不追求复刻 SimpleMem 的完整平台能力，也不引入向量数据库、embedding 模型或复杂索引服务。`nanocode` 的主目标是 coding agent runtime，记忆系统应该服务 agent 的长期上下文，而不是反过来把项目变成记忆平台。

## 设计定位

记忆系统和上下文管理必须分清楚：

```text
记忆系统：决定什么长期信息值得保存、怎么保存、怎么召回。
上下文管理：决定当前这一轮模型能看到什么、什么时候注入记忆、怎么压缩历史。
```

记忆不是强制规则。它只是旧信息和用户偏好的先验上下文。凡是涉及代码、配置、Git 状态、依赖版本的事实，模型在使用前都必须重新读取当前文件或运行命令验证。

这点接近 Claude Code 的文件化记忆思路：索引常驻、正文按需读取、记忆可人工审计。也吸收 Codex memories 的一个重要设计：记忆不只保存结论，还要保存来源和支持证据。

## 总体设计

### 先看心智模型

长期记忆不要理解成“把聊天记录存起来”。在 `nanocode` 里，一条记忆应该是一份很小的、可审计的项目笔记：

```text
正文：这条记忆到底说了什么
元数据：它属于哪类、和哪些关键词/实体/主题有关、重要性如何
证据：它为什么可信、来自用户明确要求还是模型总结
状态：它是否仍然有效、是否被新记忆替代、是否需要重新验证
```

所以 memory 模块的设计核心不是“让模型想起一切”，而是把长期信息变成可维护的小文档，并在需要时把最相关的几条交给 context 模块注入。

可以把整个系统拆成四个角色：

| 角色 | 负责什么 | 不负责什么 |
|------|----------|------------|
| `MemoryEntry` | 描述一条记忆的内容、元数据、证据和状态 | 不做文件读写 |
| `MemoryStore` | 读写 Markdown 文件，解析 frontmatter，同步 `MEMORY.md` 索引 | 不判断当前任务需要哪些记忆 |
| `MemoryRetriever` | 根据用户请求召回候选，打分，交给 LLM side-query 精选 | 不直接修改消息历史 |
| `MemoryMaintainer` | 去重、归档、衰减、标记 superseded | 不静默删除用户可能关心的信息 |

这四个角色对应四类稳定变更原因：数据结构变化、存储格式变化、召回策略变化、维护策略变化。模块划分只围绕这些真实变更原因展开，不为了“看起来架构复杂”而继续拆小文件。

### 设计回答的三个问题

#### 1. 什么值得保存

只保存当前项目文件里不容易直接推导出来的长期信息，例如：

- 用户稳定偏好。
- 用户对 agent 行为的纠正。
- 项目的长期目标、取舍和约束。
- 外部资源入口，例如 issue、dashboard、文档 URL。

不要把“当前文件里已经存在的代码事实”当成长期记忆保存。代码会变，记忆会过期。涉及代码、依赖、配置、Git 状态的记忆必须带 `requires_verification: true`，使用前让模型重新读取当前文件或运行命令验证。

#### 2. 怎么找到相关记忆

召回分两步：

```text
用户请求
  -> retrieval.build_query_plan()
  -> 本地多视角候选召回
  -> LLM side-query 从候选中选最多 5 条
  -> pack_relevant_memories()
  -> rendering.format_memories_for_injection()
  -> agent/context.py 注入当前上下文
```

这里要诚实表述：第一版不是完整语义搜索，而是“本地多视角候选 + LLM 精选”。

本地候选阶段看这些信号：

- 原始 query 的 substring 命中。
- keywords、entities、topics 命中。
- type 命中，例如 `user`、`feedback`、`project`、`reference`。
- importance、confidence、recency、access_count。

LLM side-query 只负责在候选里精选，不负责全库搜索。这样做的价值是成本低、延迟低、行为可解释；代价是如果本地候选阶段完全漏掉某条记忆，LLM 没机会选中它。因此文档和简历里不能把它说成“完整 semantic recall”。

#### 3. 怎么长期维护

记忆会重复、过期、互相冲突，所以必须有维护动作：

```text
/memory maintain 或显式维护函数
  -> 找 exact duplicate / near duplicate
  -> 按 importance、recency、access_count 做衰减
  -> 把过期记忆 archive
  -> 用 superseded_by 标记替代关系
  -> 默认 soft update，不硬删除
```

维护的目标不是“压缩得越少越好”，而是让留下来的记忆更可信、更容易解释。

### 端到端数据流

保存路径：

```text
用户反馈 / 项目经验
  -> 模型按 memory 保存规则生成 Markdown
  -> write_file 写入 memory 文件
  -> store.sync_memory_file()
  -> MEMORY.md 自动重建索引
```

召回路径：

```text
用户请求
  -> retrieval 构造 query plan
  -> 从 memory 文件和索引中取本地候选
  -> LLM side-query 精选候选
  -> 按预算打包
  -> rendering 生成注入文本和 freshness warning
  -> context 模块决定何时注入
```

维护路径：

```text
/memory maintain 或显式维护函数
  -> store 加载 active memories
  -> consolidation 生成维护建议
  -> dry-run 展示变更
  -> 用户确认或显式执行
  -> 写回文件并重建索引
```

### 模块结构

保留 `nanocode/memory/` 包，按真实变更原因划分模块：

```text
nanocode/memory/
├── __init__.py
├── types.py           # MemoryEntry、查询计划、召回结果等数据结构
├── store.py           # Markdown/frontmatter 读写、索引同步、文件状态
├── retrieval.py       # 查询规划、本地候选、多视角打分、side-query、预算打包
├── rendering.py       # 保存规则、召回注入文本、freshness warning
└── consolidation.py   # 去重、衰减、supersede、archive、dry-run
```

第一版不要继续拆 `paths.py`、`metadata.py`、`scoring.py`、`prefetch.py`。这些小模块看起来“架构化”，实际会增加跳转成本。等 `store.py` 或 `retrieval.py` 超过明显维护边界，再按真实复杂度拆。

### 核心原则

- 文件是事实来源，`MEMORY.md` 只是索引。
- 记忆正文必须自包含，脱离原对话也能理解。
- 证据不是法律意义上的证明，而是说明“这条记忆从哪里来、为什么暂时可信”。
- 召回先本地候选，再 LLM 精选，控制成本。
- 记忆注入必须有预算，不能挤占主要任务上下文。
- 旧记忆必须带 freshness warning，提醒模型验证当前代码事实。

## 详细设计

### 1. `types.py`

`types.py` 使用 `dataclass`，不引入 Pydantic。

核心结构：

```python
@dataclass
class MemoryEntry:
    memory_id: str
    name: str
    description: str
    type: str
    filename: str
    content: str
    status: str = "active"
    keywords: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    timestamp: str = ""
    importance: float = 0.5
    confidence: float = 0.7
    access_count: int = 0
    created_at: str = ""
    updated_at: str = ""
    last_accessed_at: str = ""
    superseded_by: str = ""
    source_session_id: str = ""
    source_turn: str = ""
    created_by: str = "model"
    evidence: str = ""
    confidence_reason: str = ""
    last_verified_at: str = ""
    requires_verification: bool = True
    file_path: str = ""
    mtime_ms: float = 0.0
    extra: dict[str, str] = field(default_factory=dict)
```

仍保留四类记忆：

```text
user       用户身份、偏好、知识背景
feedback   用户对 agent 行为的纠正和肯定
project    项目决策、目标、截止日期、长期约束
reference  外部资源位置，例如 URL、dashboard、issue、文档入口
```

新增字段含义：

| 字段 | 作用 |
|------|------|
| `source_session_id` | 记忆来自哪个会话 |
| `source_turn` | 来自第几轮或哪个事件 |
| `created_by` | `model`、`user`、`import`、`maintainer` |
| `evidence` | 支持证据摘要，保持短文本 |
| `confidence_reason` | 为什么给这个置信度 |
| `last_verified_at` | 最近一次验证该记忆的时间 |
| `requires_verification` | 涉及代码事实时默认需要验证 |

查询相关结构：

```python
@dataclass
class MemoryQueryPlan:
    query: str
    keywords: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    types: list[str] = field(default_factory=list)

@dataclass
class MemorySearchHit:
    entry: MemoryEntry
    score: float
    reason: str = ""

@dataclass
class RelevantMemory:
    path: str
    filename: str
    content: str
    mtime_ms: float
    type: str
    updated_at: str
    score: float = 0.0
    reason: str = ""
    evidence: str = ""
    requires_verification: bool = True
```

`MemoryQueryPlan` 不需要复杂抽象，它只是把用户 query 拆成多种检索视角。

### 2. 文件格式

继续使用 Markdown + 简单 frontmatter。因为当前 frontmatter 解析器只支持简单 `key: value`，不要写 YAML list 或嵌套对象。

推荐格式：

```markdown
---
memory_id: 20260607-a31b9c2d
name: 用户偏好简洁务实的实现风格
description: 用户希望 nanocode 代码简洁优雅务实，合理抽象，不炫技也不偷工减料
type: feedback
status: active
keywords: code style, pragmatic, maintainable
entities: nanocode
topics: engineering quality, implementation style
timestamp: 2026-06-07T00:00:00+08:00
importance: 0.85
confidence: 0.90
access_count: 0
created_at: 2026-06-07T15:30:00+08:00
updated_at: 2026-06-07T15:30:00+08:00
last_accessed_at:
superseded_by:
source_session_id: 8f2a1c4d
source_turn: user-20260607-01
created_by: model
evidence: 用户明确要求代码风格简洁优雅务实，不要炫技，不要偷工减料
confidence_reason: 直接来自用户明确指令
last_verified_at: 2026-06-07T15:30:00+08:00
requires_verification: false
---
用户希望 nanocode 的代码实现保持简洁、优雅、务实。设计时要有软件工程思维，合理抽象，按需划分模块，避免过度设计，也不能为了简单而偷工减料。

Why: 用户关注工程质量、维护成本和面试可解释性。
How to apply: 给出方案或写代码时，优先选择边界清晰、局部可测、后续可扩展的实现。
```

证据字段只保存短摘要。不要把大段对话、完整工具输出、完整文件内容塞进 frontmatter。

如果证据较多，正文可以加一段：

```markdown
## Supporting Evidence

- User explicitly requested pragmatic and maintainable implementation style on 2026-06-07.
- Applies to nanocode design and refactor discussions.
```

### 3. `store.py`

职责：

- 计算当前项目记忆目录。
- 读取和写入 Markdown 记忆文件。
- 兼容旧记忆 frontmatter。
- 维护 `MEMORY.md` 索引。
- 提供状态更新和访问统计。

路径保持：

```text
~/.nanocode/projects/{sha256(cwd)[:16]}/memory/
```

主要 API：

```python
def get_memory_dir() -> Path
def is_memory_file(path: Path) -> bool
def list_memories(include_inactive: bool = False) -> list[MemoryEntry]
def get_memory(filename: str) -> MemoryEntry | None
def save_memory(name: str, description: str, type: str, content: str, **meta: Any) -> str
def delete_memory(filename: str) -> bool
def mark_accessed(filenames: list[str]) -> None
def update_status(filename: str, status: str, superseded_by: str = "") -> bool
def update_importance(filename: str, importance: float) -> bool
def sync_memory_file(path: Path) -> None
def update_memory_index() -> None
def load_memory_index() -> str
```

兼容要求：

- 旧文件缺字段时用默认值，不批量重写。
- 未知字段进入 `extra`，重写时尽量保留。
- `keywords`、`entities`、`topics` 用逗号分隔字符串。
- `requires_verification` 解析 `true/false/yes/no/1/0`，解析失败时默认 `true`。
- 解析单个坏文件失败不能影响其他记忆。

索引格式：

```markdown
# Memory Index

- **[用户偏好简洁务实的实现风格](feedback_user_prefers_pragmatic_style.md)** (feedback) [importance=0.85] - 用户希望 nanocode 代码简洁优雅务实，合理抽象
```

索引只展示 active 记忆。`superseded` 和 `archived` 文件保留在磁盘上，但不进入默认索引。

### 4. `retrieval.py`

本轮重点改造在这里。

#### 查询规划

新增：

```python
def build_query_plan(query: str) -> MemoryQueryPlan
```

第一版先用规则实现，不额外调用 LLM：

- 原 query 进入 `query`。
- 英文、数字、下划线、短横线切成 token。
- 中文短句保留为 substring 匹配依据。
- 根据少量内置同义词扩展常见 coding-agent 主题。

示例：

```python
TOPIC_EXPANSIONS = {
    "部署": ["deploy", "release", "ci", "cd", "pipeline"],
    "上线": ["deploy", "release", "ci", "cd", "pipeline"],
    "测试": ["test", "pytest", "unittest", "ci"],
    "记忆": ["memory", "recall", "preference"],
    "沙箱": ["sandbox", "microsandbox", "isolation"],
}
```

不要做大型中文分词依赖。这里的目标是补足常见工程词汇，不是做搜索引擎。

后续可选增强：

```python
async def build_query_plan_with_side_query(query: str, side_query: SideQueryFn) -> MemoryQueryPlan
```

只有本地候选太少时才启用，避免每轮多一次 API 调用。

#### 多视角候选

候选不只看用户原句，也看 query plan 的：

- keywords
- entities
- topics
- types
- 原始 query substring
- 记忆 metadata
- importance、confidence、recency、access_count

建议分数：

```text
score =
  raw_query_substring * 3.0
  + keyword_hits * 1.0
  + metadata_hits * 0.7
  + entity_hits * 1.2
  + topic_hits * 0.9
  + type_boost
  + importance * 0.5
  + confidence_factor
  + recency_bonus * 0.3
  + access_bonus
```

这里不要追求数学精致，关键是可解释。每个命中都要能写进 `reason`：

```text
matched keywords: memory, recall; matched entity: nanocode; high importance
```

#### side-query 精选

保留当前思路：本地 top N 候选交给 LLM 选最多 5 条。

prompt 应明确：

```text
You are selecting long-term memories for a coding agent.
Only select memories that are clearly useful for the current user request.
Prefer memories with matching entities/topics and supporting evidence.
Do not select stale project facts unless directly relevant.
Return JSON: {"selected_memories": ["filename.md"]}.
```

注意：side-query 是重排，不是全库语义搜索。文档中必须诚实描述为：

```text
多视角本地候选 + LLM 精选
```

不要宣称“完整语义召回”。

#### 预算打包

保留：

```python
MAX_LOCAL_CANDIDATES = 20
MAX_SELECTED_MEMORIES = 5
MAX_FALLBACK_MEMORIES = 3
MAX_MEMORY_BYTES_PER_FILE = 4096
MAX_SESSION_MEMORY_BYTES = 60 * 1024
MAX_INJECTED_MEMORY_TOKENS = 1200
```

单条记忆超限先截断正文，总预算满则停止加入。

估算 token 继续用：

```python
len(text) // 4
```

不要引入 tokenizer 依赖。

#### 异步预取

`MemoryPrefetch` 和 `start_memory_prefetch()` 继续放在 `retrieval.py`。

门控：

- 子 agent 不启动。
- 单词或极短输入不启动。
- active memory 为空不启动。
- 本会话记忆注入预算已满不启动。
- 没有事件循环不启动。

消费仍由 `agent/context.py` 控制，因为“什么时候注入当前消息历史”属于上下文管理。

### 5. `rendering.py`

职责：

- 渲染记忆系统保存规则。
- 渲染召回注入内容。
- 生成 freshness warning。

保存规则要强调：

```text
记忆只保存不可从当前项目推导的信息。
相对时间必须转绝对时间。
正文必须自包含。
涉及代码事实必须写 requires_verification: true。
不要保存 secrets、token、password、private key。
feedback 记忆必须包含 Why 和 How to apply。
```

召回注入格式：

```text
<system-reminder>
Relevant long-term memory. Use it as prior context, not as live state.
Verify code-related claims against current files before relying on them.

Memory: /path/to/file.md
Type: feedback
Updated: 2026-06-07T15:30:00+08:00
Freshness: Memory saved today.
Score reason: matched entity: nanocode; high importance
Evidence: 用户明确要求简洁务实的代码风格
Requires verification: false

...
</system-reminder>
```

超过 1 天的记忆加入：

```text
This memory is N days old. Memories are point-in-time observations, not live state.
```

不要把整个 `MEMORY.md` 长期塞进 stable system prompt。记忆保存规则和当前索引应通过 context startup 或 memory attachment 注入，具体由 `context` 方案决定。

### 6. `consolidation.py`

维护逻辑显式触发，不默认后台自动跑。

动作：

- exact duplicate：正文规范化后完全相同。
- near duplicate：同类型 token Jaccard 相似度超过阈值。
- decay：长期未访问、非高重要度的记忆降低 importance。
- archive：低重要度、长期未访问的记忆标记 archived。
- supersede：重复记忆标记 superseded，保留 `superseded_by`。

不要硬删除文件。

阈值：

```text
NEAR_DUPLICATE_THRESHOLD = 0.82
FEEDBACK_DUPLICATE_THRESHOLD = 0.90
DECAY_AFTER_DAYS = 45
DECAY_FACTOR = 0.05
MIN_IMPORTANCE = 0.15
ARCHIVE_AFTER_DAYS = 180
PINNED_IMPORTANCE = 0.95
```

证据字段对维护的影响：

- `confidence` 高、`evidence` 非空的记忆更倾向保留。
- `feedback` 类型不要轻易合并，除非相似度很高。
- `requires_verification=true` 不表示低质量，只表示使用时要验证。

## 硬性约束

- 不新增第三方依赖。
- 不引入向量数据库、embedding、BM25 库。
- 不改 agent 主循环语义。
- 不让子 agent 写入或召回长期记忆。
- 不把记忆当成权限或强制规则。
- 不默认自动从每次会话生成记忆。
- 不把 secrets 保存到记忆。
- 不把所有记忆正文塞进 system prompt。
- 记忆失败不能中断主任务。
- 所有旧记忆文件必须可读。

## 隐含要求

- 记忆必须可人工打开、理解、修改。
- 每条记忆应有足够上下文，避免“这个”“上次”“那个方案”。
- 时间表达要绝对化。
- 记忆召回必须有理由，便于调试误召回。
- 记忆注入要短，不能喧宾夺主。
- 涉及代码事实时必须提醒模型重新验证。
- `MEMORY.md` 是索引，不是数据库。
- 维护逻辑默认 dry-run，用户显式 apply 才改状态。

## 不能做什么

- 不能宣传为“完整语义召回”，当前路线是“多视角本地候选 + LLM 精选”。
- 不能为了显得高级引入向量库。
- 不能把用户每句话都保存成记忆。
- 不能保存 API key、token、SSH key、`.env` 内容。
- 不能在工具层重复解析记忆 frontmatter。
- 不能让 side-query 失败影响主模型回答。
- 不能让旧记忆覆盖当前文件事实。
- 不能把 memory prompt 写成一堆空泛原则，必须告诉模型保存格式和反例。

## 可能踩坑

### 候选阶段漏召回

如果本地候选没有找到，side-query 看不到相关记忆。方案 B+ 通过 query plan 和主题扩展缓解，但仍不是 dense semantic search。文档中要诚实说明。

### 中文分词

不要引入中文分词库。先用 substring、少量主题扩展和 metadata 解决主要场景。真正需要中文检索时，再评估是否引入依赖。

### 证据字段过长

证据字段如果保存完整对话，会污染索引和上下文。只保存短摘要，详细证据可以放正文短段落。

### 记忆过期

项目事实很容易过期。`freshness warning` 和 `requires_verification` 必须保留，尤其是 `project` 类型。

### 误把记忆当规则

用户偏好可以指导输出风格，但不能覆盖本轮显式要求。项目规则应放 `CLAUDE.md` 或仓库文档，记忆只是辅助上下文。

### frontmatter 不是完整 YAML

当前解析器简单，字段值不要写复杂结构。证据列表不要放 YAML list，使用短字符串或正文段落。

### 维护误合并

`feedback` 记忆相似也可能边界不同。feedback 的 near duplicate 阈值要更高，且默认 soft supersede，不删除。

## 实施步骤

1. 更新 `types.py`，加入来源、证据、查询计划字段。
2. 更新 `store.py`，兼容新 frontmatter 字段和旧文件默认值。
3. 更新 `retrieval.py`，加入 `MemoryQueryPlan`、主题扩展、多视角打分和更清楚的 reason。
4. 更新 `rendering.py`，把 evidence、requires_verification、freshness warning 注入召回文本。
5. 更新 `consolidation.py`，让 evidence/confidence 影响保留策略。
6. 更新 `agent/context.py` 的消费逻辑，确保注入预算和 accessed 标记仍然正确。
7. 后续再补 recall eval 和集成测试，不阻塞第一阶段重构。

## 验收标准

- 旧记忆文件可读，新字段缺失时有默认值。
- 新保存的记忆包含来源和证据字段。
- 查询“部署流程”这类非直匹配请求时，可以通过主题扩展召回 `ci/cd/release` 相关候选。
- side-query 失败时回退到本地 top 3。
- 召回注入包含 reason、evidence、freshness、requires verification。
- 记忆注入不超过预算。
- 子 agent 不触发长期记忆。
- consolidation 默认 dry-run，不硬删除文件。
