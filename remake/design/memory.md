# Memory 重构方案

## 目标

把当前 `nano_code/memory.py` 从“单文件工具集合”重构成一个职责清晰、可测试、可维护的结构化长期记忆系统。

本轮重构吸收 SimpleMem 中低侵入、高收益的思想：

- 自包含记忆单元：记忆正文脱离上下文也能理解，并带 `keywords`、`entities`、`topics`、`timestamp` 等检索元数据。
- 写入侧压缩：保存记忆时强调消解代词、相对时间转绝对时间、避免低价值信息。
- 混合召回：先用本地关键词、元数据、新鲜度、重要性打分得到候选，再交给 `sideQuery` 精选。
- 预算打包：按字节和粗略 token 预算贪心注入，避免长期记忆挤占主上下文。
- 记忆维护：做 exact duplicate、near duplicate、importance decay、supersede，不做硬删除。

本轮不追求复刻 SimpleMem 的完整平台能力，不引入 LanceDB、embedding 模型、Pydantic、MCP 服务、多模态、自进化优化器。先把 `nano_code` 自己的长期记忆边界打稳。

## 总体设计

### 结论

删除原有 `nano_code/memory.py` 单文件实现，改为 `nano_code/memory/` 包。

不保留旧 `memory.py` 作为兼容门面。内部调用点要显式迁移到新模块，避免继续把所有职责挤回一个 facade。

建议结构：

```text
nano_code/memory/
├── __init__.py
├── types.py
├── store.py
├── retrieval.py
├── rendering.py
└── consolidation.py
```

模块职责：

| 模块 | 职责 |
|------|------|
| `types.py` | 记忆数据结构、策略、召回结果、维护结果 |
| `store.py` | 路径、元数据解析、Markdown 文件读写、`MEMORY.md` 索引同步 |
| `retrieval.py` | 本地打分、sideQuery 精选、预算打包、异步预取 |
| `rendering.py` | system prompt 片段、注入文本渲染 |
| `consolidation.py` | 去重、近似重复、衰减、supersede、archive、dry-run |

`__init__.py` 不作为旧 API 的兼容门面，只放稳定导出和包说明。业务调用点应尽量从具体模块导入，例如：

```python
from nano_code.memory.store import list_memories
from nano_code.memory.retrieval import MemoryPrefetch, start_memory_prefetch
from nano_code.memory.rendering import build_memory_prompt_section
```

这样做的目的不是制造更多文件，而是把“存储、召回、渲染、维护”这些主要变更原因分开。第一版不拆 `paths.py`、`metadata.py`、`index.py`、`scoring.py`、`prefetch.py`、`extraction.py`，避免出现只有两三个函数的小文件。后续如果 `store.py` 或 `retrieval.py` 明显变重，再按真实复杂度拆分。

### 运行时边界

记忆系统仍然只服务主 Agent：

- 主 Agent 可以读写和召回长期记忆。
- 子 Agent 不触发长期记忆召回，不更新长期记忆访问统计。
- skill、MCP、tool runtime、session 保存不改变行为，只改它们引用记忆能力的 import 和调用点。

现有用户可见能力保持：

- `/memory` 仍能列出记忆。
- system prompt 仍包含记忆目录、保存规则和当前索引。
- 写入记忆目录下的 `.md` 文件后仍自动更新 `MEMORY.md`。
- 用户回合仍通过异步预取注入相关记忆，不能阻塞主响应。

## 硬性约束

### 不改变其他功能实现

重构范围只限长期记忆系统和必要调用点：

- 可以改 `nano_code/agent/context.py` 的记忆 import 和调用。
- 可以改 `nano_code/prompt.py` 的记忆 prompt import。
- 可以改 `nano_code/tools.py` 的 memory index 更新调用。
- 可以改 `nano_code/__main__.py` 的 `/memory` 命令 import 和显示字段。

不能改：

- agent 主循环语义。
- Anthropic/OpenAI 后端协议。
- skill 调用、active skill、sub-agent 逻辑。
- MCP 连接和工具注册逻辑。
- 权限模式、工具执行权限、文件编辑安全检查。
- session 保存和恢复格式，除非只是补充可选字段且不影响旧格式读取。

### 轻依赖

第一版不新增第三方依赖。

原因：

- 当前项目依赖很少，只有 `anthropic`、`openai`、`rich`。
- 记忆规模预期不大，本地 Markdown 扫描足够。
- SimpleMem 的重依赖适合完整记忆平台，不适合作为 `nano_code` 第一版重构基础。

### 兼容旧记忆文件

旧记忆只有：

```text
name
description
type
```

读取时必须兼容。缺失字段用默认值补齐，不批量重写旧文件。

默认值建议：

```text
status: active
importance: 0.5
confidence: 0.7
keywords: []
entities: []
topics: []
created_at: 文件 mtime 对应 ISO 时间
updated_at: 文件 mtime 对应 ISO 时间
access_count: 0
last_accessed_at: ""
superseded_by: ""
```

### 索引是目录，不是数据库

`MEMORY.md` 继续作为给模型和人类看的索引，不作为唯一事实来源。

事实来源仍是每个 Markdown 文件。索引损坏或缺失时，应能通过扫描记忆文件重建。

### 失败不影响主循环

记忆召回、解析、索引、维护都必须是 best-effort。

- 解析单个坏文件失败，不影响其他文件。
- sideQuery 失败，不影响主响应。
- 索引更新失败，不影响工具写文件返回。
- consolidation 出错，不删除文件，不中断 REPL。

## 隐含要求

### 记忆只保存不可从当前项目推导的信息

这是当前记忆系统已有原则，也是必须保留的核心边界。

应该保存：

- 用户偏好和明确反馈。
- 项目决策、约束、截止日期。
- 外部资源位置。
- 未来需要跨会话记住的工作背景。

不应该保存：

- 当前代码结构。
- git 历史。
- 测试命令，如果项目文件已经写明。
- 本轮临时调试过程。
- 可通过读文件立即得到的信息。

### 相对时间必须转绝对时间

如果记忆中出现“明天”“下周四”“月底前”，保存前应要求模型转为绝对日期。

保存时 system prompt 要明确当前日期，让模型知道如何转换。读取时仍保留 freshness warning，提醒记忆是时间切片。

### 记忆正文必须自包含

正文不能写：

```text
用户喜欢这个方案。
```

应该写：

```text
用户喜欢把 nano_code 的记忆系统拆成独立模块，并保留文件式存储，不希望引入重依赖。
```

这来自 SimpleMem 的 `lossless_restatement` 思想，但在 `nano_code` 中用 Markdown 正文承载，不引入 Pydantic 模型。

## 详细设计

### 1. 数据结构

`types.py` 定义核心结构，使用 `dataclass`，不使用 Pydantic。

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
```

`type` 仍使用当前四类：

```text
user
feedback
project
reference
```

不新增 SimpleMem 的 `episodic/semantic/project_state` 等类型。原因是 `nano_code` 已经有稳定的四分类，直接换分类会影响 prompt 和用户心智。

新增字段说明：

| 字段 | 用途 |
|------|------|
| `memory_id` | 稳定引用，避免文件重命名后丢失身份 |
| `status` | `active`、`superseded`、`archived` |
| `keywords` | 本地关键词召回 |
| `entities` | 项目、工具、人物、系统等实体 |
| `topics` | 主题聚合和近似重复检测 |
| `timestamp` | 记忆事实发生时间，不等于文件修改时间 |
| `importance` | 召回加权和维护衰减 |
| `confidence` | 对抽取质量的置信度 |
| `access_count` | 召回反馈 |
| `last_accessed_at` | 衰减和维护依据 |
| `superseded_by` | soft delete 链接 |

### 2. 文件格式

继续使用 Markdown + 简单 frontmatter。

示例：

```markdown
---
memory_id: 20260605-8f2a1c
name: 用户偏好简洁务实的代码风格
description: 用户希望 nano_code 代码简洁务实，不炫技，不偷工减料
type: feedback
status: active
keywords: code style, pragmatic, maintainable
entities: nano_code
topics: code quality, implementation style
timestamp: 2026-06-05T00:00:00+08:00
importance: 0.8
confidence: 0.9
access_count: 0
created_at: 2026-06-05T15:30:00+08:00
updated_at: 2026-06-05T15:30:00+08:00
last_accessed_at:
superseded_by:
---
用户希望 nano_code 的代码风格保持简洁务实，合理抽象，按需划分模块，不炫技，也不能为了简单而偷工减料。

Why: 用户关注工程质量、可维护性和长期扩展。
How to apply: 设计和实现时优先选择清楚、局部、可测试的方案，避免不必要的依赖和复杂抽象。
```

字段解析规则：

- `keywords/entities/topics` 用逗号分隔，不引入 YAML list。
- 空字符串表示无值。
- 数字字段解析失败时回退默认值。
- 未知字段保留在 `extra` 中，重写文件时尽量保留，避免破坏未来扩展。

### 3. 存储层：路径、元数据、文件和索引

`store.py` 负责所有本地持久化相关逻辑，包括路径、frontmatter 元数据、Markdown 文件读写、`MEMORY.md` 索引同步。

路径保留当前目录 hash 策略：

```text
~/.nano-code/projects/{sha256(cwd)[:16]}/memory/
```

原因：

- 同一项目稳定命中同一记忆空间。
- 不同项目互不污染。
- 路径不会暴露完整项目名或特殊字符。

暂不做全局用户记忆目录。用户偏好仍存到当前项目记忆中。后续如果要做全局偏好，需要单独设计优先级和冲突规则。

主要 API：

```python
def get_memory_dir() -> Path
def is_memory_file(path: Path) -> bool
def list_memories(include_inactive: bool = False) -> list[MemoryEntry]
def get_memory(filename: str) -> MemoryEntry | None
def save_memory(name: str, description: str, type: str, content: str, **meta) -> str
def delete_memory(filename: str) -> bool
def mark_accessed(filenames: list[str]) -> None
def update_status(filename: str, status: str, superseded_by: str = "") -> bool
def sync_memory_file(path: Path) -> None
def update_memory_index() -> None
def load_memory_index() -> str
```

注意：

- `delete_memory()` 可以保留用于显式删除，但 consolidation 不调用它。
- `sync_memory_file()` 用于工具写入后补齐索引，不应强行改写用户文件，除非缺失最关键字段且调用方明确要求。
- `mark_accessed()` 更新 `access_count` 和 `last_accessed_at`，失败静默。
- `update_memory_index()` 只展示 active 记忆，索引是目录，不是数据库。

索引只展示 active 记忆：

```markdown
# Memory Index

- **[用户偏好简洁务实的代码风格](feedback_user_prefers_pragmatic_code.md)** (feedback) [importance=0.80] — 用户希望 nano_code 代码简洁务实，不炫技，不偷工减料
```

保留现有截断：

```text
MAX_INDEX_LINES = 200
MAX_INDEX_BYTES = 25000
```

截断提示要带修复指引：

```text
[... truncated, too many memory entries. Keep each memory index entry to one short line and archive stale memories ...]
```

`store.py` 会比单纯文件读写略重，但它仍属于同一个变更原因：本地记忆持久化。第一版不要再拆 `paths.py`、`metadata.py`、`index.py`。如果后续 `store.py` 超过约 350 行，且路径、元数据、索引各自出现独立测试压力，再拆不迟。

### 4. 写入侧压缩提示

第一版不做独立 `extraction.py`。写入侧压缩先通过 `rendering.py` 里的 system prompt 规则表达，实际保存仍由模型使用 `write_file` 或后续显式命令完成。

system prompt 中增加保存要求：

```text
When saving a memory:
- Write a self-contained memory. Avoid pronouns that require prior context.
- Convert relative dates to absolute dates using the current date.
- Add keywords, entities, topics, timestamp when useful.
- Do not save code facts that can be recovered by reading files or git history.
- For feedback memories, include Why and How to apply.
```

为什么不第一版自动抽取：

- 自动抽取会改变 agent 行为，可能在用户未要求时写长期记忆。
- 自动抽取需要额外 LLM 调用，成本和失败点增加。
- 当前 `nano_code` 的记忆保存是模型通过 `write_file` 显式完成，先强化规则更稳。

后续如果需要自动会话抽取，再新增独立模块或从 `rendering.py` 迁出：

```python
async def propose_memories_from_session(turns, side_query) -> list[MemoryDraft]
```

但默认关闭，只通过显式命令或用户请求触发。

### 5. 召回层：本地候选打分

`retrieval.py` 借鉴 EvolveMem 的轻量混合检索，不做 embedding。第一版不拆 `scoring.py`，tokenize 和评分函数作为 `retrieval.py` 的私有函数即可。

候选分数：

```text
score =
  keyword_score * 1.0
  + metadata_score * 0.6
  + type_boost
  + importance * 0.5
  + recency_bonus * 0.3
  + access_bonus
```

打分来源：

- `keyword_score`：query token 命中 name、description、content、keywords。
- `metadata_score`：命中 entities、topics、timestamp、type。
- `type_boost`：`feedback` 和 `user` 略高，`reference` 按 query 命中决定。
- `importance`：用户或维护流程可调整。
- `recency_bonus`：最近更新的记忆略加分，但不能压过强相关旧记忆。
- `access_bonus`：经常被召回的记忆略加分。

tokenize 要简单：

- 英文、数字、下划线、短横线连续成 token。
- 中文不强行分词，先用整段 substring 命中和英文 token 混合。
- 过滤长度过短的英文 token，例如 1 字符 token。

本地候选数量建议：

```text
MAX_LOCAL_CANDIDATES = 20
MAX_SELECTED_MEMORIES = 5
```

### 6. 召回层：sideQuery 精选

`retrieval.py` 负责组合本地候选和 sideQuery。

流程：

```text
用户 query
→ list active memories
→ 本地打分
→ top 20 候选
→ sideQuery 从候选中选最多 5 条
→ 加载正文
→ 预算裁剪
→ 返回 RelevantMemory
```

sideQuery prompt 要比当前版本更明确：

```text
You are selecting long-term memories for a coding agent.
Only select memories that are clearly useful for the current user request.
Prefer self-contained memories with matching entities/topics.
Do not select stale project facts unless they are directly relevant.
Return JSON: {"selected_memories": ["filename.md"]}
```

失败回退：

- sideQuery 返回非 JSON：使用本地 top 结果。
- sideQuery 选了不存在的文件：忽略。
- sideQuery 异常或取消：取消时返回空，普通异常使用本地 top 结果。

回退数量建议更保守：

```text
MAX_FALLBACK_MEMORIES = 3
```

这样不会因为 sideQuery 故障注入太多无关记忆。

### 7. 召回层：预算打包

`retrieval.py` 提供预算裁剪：

```python
def pack_relevant_memories(
    memories: list[RelevantMemory],
    max_bytes: int,
    max_estimated_tokens: int,
) -> list[RelevantMemory]
```

预算建议：

```text
MAX_MEMORY_BYTES_PER_FILE = 4096
MAX_SESSION_MEMORY_BYTES = 60 * 1024
MAX_INJECTED_MEMORY_TOKENS = 1200
MAX_SELECTED_MEMORIES = 5
```

估算 token 不引入 tokenizer，使用粗略估计：

```python
def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)
```

SimpleMem Cross 用 `split()` 估算 token，但对中文不友好。`len(text) // 4` 对中英文混合更保守。

打包顺序：

1. sideQuery 或本地分数排序后的顺序。
2. 逐条加入。
3. 单条超限先截断正文。
4. 总预算满则停止。

### 8. 注入渲染

`rendering.py` 负责两类渲染：

```python
def build_memory_prompt_section() -> str
def format_memories_for_injection(memories: list[RelevantMemory]) -> str
```

注入格式保留 `<system-reminder>`，避免改变主循环消息结构：

```text
<system-reminder>
Relevant long-term memory. Use it as prior context, but verify code-related claims against current files.

Memory: /path/to/file.md
Type: feedback
Updated: 2026-06-05T15:30:00+08:00
Freshness: saved today
Score reason: matched entities: nano_code; high importance

...
</system-reminder>
```

超过 1 天的记忆继续加 freshness warning。

注意：记忆注入是追加到当前用户消息，不新增 system message。这保留现有 Anthropic/OpenAI 两套消息历史的合法形状。

### 9. 异步预取

第一版不做独立 `prefetch.py`。`MemoryPrefetch` 和 `start_memory_prefetch()` 放在 `retrieval.py`，因为预取本质上只是召回的异步启动和门控。

主要 API：

```python
class MemoryPrefetch:
    task: asyncio.Task
    consumed: bool

def start_memory_prefetch(query, side_query, already_surfaced, session_memory_bytes) -> MemoryPrefetch | None
```

门控：

- 子 Agent 不启动。
- 单 token 输入不启动。
- 当前会话记忆预算已满不启动。
- 没有 active 记忆不启动。
- 当前事件循环不可用时不启动。

消费逻辑仍由 `agent/context.py` 控制：

- task done 后非阻塞消费。
- 注入到最后一条用户消息。
- 更新 `_already_surfaced_memories` 和 `_session_memory_bytes`。
- 调用 `store.mark_accessed()` 更新访问统计。

### 10. 记忆维护

`consolidation.py` 做显式维护，不自动频繁运行。

主要 API：

```python
def consolidate_memories(dry_run: bool = True) -> ConsolidationResult
```

维护动作：

1. exact duplicate：同类型、正文规范化后一致，保留 importance 更高或更新的条目。
2. near duplicate：同类型、token Jaccard 相似度超过阈值，保留 importance 更高或更新的条目。
3. stale working facts：暂不单独实现，因为当前没有 working_summary 类型。
4. importance decay：长时间未访问且非高重要度记忆降低 importance。
5. archive candidate：低 importance、长期未访问的记忆只标记 `archived`，不删除。

状态变更：

```text
active -> superseded
active -> archived
```

不做：

```text
unlink file
```

阈值建议：

```text
NEAR_DUPLICATE_THRESHOLD = 0.82
DECAY_AFTER_DAYS = 45
DECAY_FACTOR = 0.05
MIN_IMPORTANCE = 0.15
ARCHIVE_AFTER_DAYS = 180
```

CLI 后续可以增加：

```text
/memory maintain
/memory maintain --apply
```

第一版可以只实现函数和测试，不马上加命令。若加命令，默认 dry-run，必须用户显式 `--apply` 才改状态。

### 13. 工具写入后的索引同步

当前 `tools.py` 内部有 `_auto_update_memory_index()`，使用正则重复解析 memory frontmatter。

重构后应删除这套重复逻辑，改为：

```python
from .memory.store import is_memory_file, update_memory_index

def _auto_update_memory_index(file_path: str) -> None:
    if is_memory_file(Path(file_path)):
        update_memory_index()
```

不要在 `tools.py` 里解析 memory 文件。工具层只判断“这次写入是否影响 memory index”，具体索引逻辑归 `memory.store`。

### 14. 调用点迁移

需要改这些 import：

`nano_code/prompt.py`：

```python
from .memory.rendering import build_memory_prompt_section
```

`nano_code/agent/context.py`：

```python
from ..memory.retrieval import MemoryPrefetch, start_memory_prefetch
from ..memory.rendering import format_memories_for_injection
```

`nano_code/tools.py`：

```python
from .memory.store import get_memory_dir, is_memory_file, update_memory_index
```

`nano_code/__main__.py`：

```python
from .memory.store import list_memories
```

旧的 `from .memory import ...` 不保留。这样可以尽早暴露遗漏调用点，避免半新半旧的结构长期存在。

## 不能做什么

### 不能直接引入 SimpleMem 作为依赖

不能：

```python
from simplemem import SimpleMem
```

原因：

- 会引入 LanceDB、sentence-transformers、Pydantic 等复杂依赖。
- 会改变 `nano_code` 的安装体积和启动行为。
- 会让记忆召回依赖外部模型下载和向量索引。
- SimpleMem 的目标是完整记忆平台，`nano_code` 当前需要的是轻量 coding agent 记忆。

### 不能新增自动记忆行为

不能在每轮对话结束后自动抽取并写入长期记忆。

原因：

- 用户没有授权时自动持久化，边界不清。
- 会产生大量低价值记忆。
- 会把当前代码状态、临时调试信息写入长期记忆，制造漂移。

第一版只强化模型“何时应该保存”的规则，实际保存仍通过工具写文件或显式命令完成。

### 不能硬删除维护命中的记忆

consolidation 只能 soft delete：

```text
status: superseded
superseded_by: <memory_id>
```

或：

```text
status: archived
```

不能直接删除文件。用户可以之后手动清理。

### 不能把 `MEMORY.md` 当数据库

不能从 `MEMORY.md` 反推完整记忆状态。它只是索引展示。

所有状态更新必须作用于具体记忆文件。

### 不能让记忆召回阻塞主响应

不能为了等 sideQuery 结果而卡住首轮模型响应。

当前设计是在模型回合循环中非阻塞消费预取结果，这个体验要保留。

### 不能用复杂抽象掩盖简单逻辑

不要引入 provider、plugin、strategy class hierarchy 这类重抽象。

当前需要的是清楚的函数和小模块，不是框架。

## 可能踩坑的地方

### 1. Python 模块名冲突

从 `nano_code/memory.py` 改成 `nano_code/memory/` 包时，必须删除旧文件，否则 Python import 会冲突。

实施顺序要注意：

```text
同一个变更里创建 memory/ 包
同一个变更里迁移调用点
同一个变更里删除 memory.py
跑 compileall
```

不要把“创建 `memory/` 包”和“删除 `memory.py`”拆成两个可运行状态。如果同名文件和目录同时存在，import 行为会不符合预期。

### 2. 内部 import 遗漏

当前多处引用 `.memory`：

- `prompt.py`
- `tools.py`
- `agent/context.py`
- `__main__.py`
- 文档和测试

不保留兼容门面后，遗漏会直接报错。应使用 `rg "from .*memory|import .*memory"` 全量检查。

### 3. tools.py 重复索引逻辑

如果忘记删除 `_auto_update_memory_index()` 内部正则解析，就会出现两套索引规则。

后果：

- 新元数据在 `store.py` 中有效，但工具写入后索引不一致。
- 坏文件处理行为不同。
- 后续维护困难。

### 4. frontmatter 简单解析器的边界

当前 frontmatter 解析不支持完整 YAML。

因此新字段必须保持简单：

```text
keywords: a, b, c
importance: 0.8
```

不要写：

```yaml
keywords:
  - a
  - b
```

如果用户手写了复杂 YAML，第一版可以当作普通文本或解析为空，不应该崩溃。

### 5. 中文检索

简单 tokenizer 对中文分词效果有限。需要同时支持：

- 英文 token 匹配。
- 原始 query 子串匹配。
- name/description/content 的直接包含匹配。

不要假设所有 query 都能拆成空格词。

### 6. 重要性和新鲜度可能压过相关性

importance、recency 只能加权，不能让不相关记忆靠高重要性被注入。

本地 scoring 应先要求至少一种相关命中：

```text
keyword hit
metadata hit
substring hit
```

没有相关命中的记忆不能只靠 importance 入选。

### 7. sideQuery 输出不稳定

模型可能输出：

- 非 JSON。
- JSON 包在 markdown 代码块中。
- 文件名拼错。
- 选择超过数量。

必须做 JSON 提取和 filename 白名单过滤。

### 8. 访问统计写回引起 mtime 变化

`mark_accessed()` 会更新文件，从而改变 mtime。如果 list 默认按 mtime 排序，会导致“最近被访问”误认为“最近被编辑”。

解决：

- `MemoryEntry.updated_at` 作为内容更新时间。
- 文件 mtime 只作为旧文件缺省值。
- list 展示可按 `updated_at`，不要按访问写回后的 mtime。

第一版如果不想复杂化，也可以先不持久化 `access_count`，只在会话内记录访问；但这样无法做长期衰减。建议持久化，并明确排序依据。

### 9. 写回文件可能破坏用户格式

更新 `access_count` 或 `status` 时需要重写 frontmatter。简单重写会改变字段顺序和正文空行。

建议：

- 只在明确状态更新或访问统计时重写。
- 使用固定字段顺序，保持可读。
- 未知 meta 字段追加保留。
- 正文原样保留。

### 10. consolidation 误判重复

near duplicate 用 token overlap 容易误伤短记忆。

规则：

- 少于 8 个有效 token 的记忆不做 near duplicate。
- 不同 type 不合并。
- `importance >= 0.95` 的记忆不自动 supersede。
- `feedback` 类型合并要更保守，因为两条反馈可能边界不同。

### 11. 日期和时区

环境时区可能不是用户所在地。`nano_code` 运行时应使用系统当前日期生成 prompt，但保存相对日期时要要求模型写绝对日期。

元数据统一 ISO 字符串即可，不要引入复杂 datetime 库。

### 12. 记忆注入和 compact 的顺序

当前流程是在回合循环里 `_run_compression_pipeline()` 后 `_consume_memory_prefetch()`。重构不要改变这个顺序，避免 compact 后消息形状异常。

## 实现顺序

### 第 1 步：一次性完成包转换

因为不保留旧 `memory.py` 兼容门面，包转换应在同一个变更里完成，避免 `nano_code/memory.py` 和 `nano_code/memory/` 同时存在导致 import 解析混乱。

新增：

```text
nano_code/memory/__init__.py
nano_code/memory/types.py
nano_code/memory/store.py
nano_code/memory/rendering.py
nano_code/memory/retrieval.py
```

删除：

```text
nano_code/memory.py
```

同步更新所有调用点：

- `prompt.py` 改为从 `memory.rendering` 导入。
- `agent/context.py` 改为从 `memory.retrieval` 和 `memory.rendering` 导入。
- `tools.py` 改为从 `memory.store` 导入路径和索引同步函数。
- `__main__.py` 改为从 `memory.store` 导入 `list_memories()`。

验证：

```bash
rg "from .*memory import|import .*memory" nano_code test
python -m compileall nano_code
python -m unittest discover -s test
```

### 第 2 步：补齐 store 测试

覆盖 `store.py` 的文件访问和索引逻辑：

- `get_memory_dir()`
- `is_memory_file()`
- `list_memories()`
- `save_memory()`
- `delete_memory()`
- `load_memory_index()`
- `update_memory_index()`
- `mark_accessed()`
- `update_status()`

测试重点：

- 保存记忆会创建文件。
- 保存后索引更新。
- 旧格式记忆可读取。
- 坏 frontmatter 不影响其他记忆。
- 索引行数和字节截断生效。
- `tools.py` 写普通项目文件不触发 memory index 更新。

### 第 3 步：补齐 rendering 测试

- `build_memory_prompt_section()`
- `format_memories_for_injection()`
- freshness warning

测试重点：

- system prompt 中仍有记忆说明和索引。
- 保存规则包含自包含记忆、绝对日期、元数据和不要保存可推导信息。
- 旧记忆和新记忆都能渲染。
- 超过 1 天的记忆带 freshness warning。

### 第 4 步：补齐 retrieval 测试

- 先本地打分。
- 再 sideQuery 精选。
- sideQuery 失败回退。
- 加预算打包。
- 保留 `MemoryPrefetch` 和 `start_memory_prefetch()`。
- 更新 `agent/context.py` import。

测试：

- 本地命中 keywords/entities/topics。
- inactive 记忆不召回。
- already_surfaced 不召回。
- sideQuery 返回非法 JSON 时回退。
- 单条超大正文会截断。
- 子 Agent 不预取。
- 会话预算门控。
- 非阻塞消费。

### 第 5 步：加入 consolidation

新增：

```text
nano_code/memory/consolidation.py
```

先实现 dry-run 和测试，再决定是否加 `/memory maintain` 命令。

测试：

- exact duplicate 识别。
- near duplicate 阈值。
- high importance 不自动 supersede。
- dry-run 不改文件。
- apply 只改 status，不删除文件。

### 第 6 步：文档更新

更新：

- `docs/08-memory.md`
- `docs/15-code-reading-guide.md`
- `docs/00-introduction.md` 中 memory 文件描述

不需要大改其他功能文档。

## 验证方案

基础验证：

```bash
python -m compileall nano_code
python -m unittest discover -s test
```

手工验证：

```text
1. 启动 nano-code。
2. 输入 /memory，确认旧记忆可列出。
3. 让模型保存一条 feedback 记忆。
4. 确认记忆文件有新元数据。
5. 确认 MEMORY.md 自动更新。
6. 下一轮问相关问题，确认相关记忆被注入。
7. 删除或 archive 一条记忆，确认不再召回。
```

回归关注：

- skill 调用不受影响。
- sub-agent 不触发记忆召回。
- OpenAI-compatible 后端消息格式不受影响。
- Anthropic tool_use/tool_result 配对不受影响。
- `write_file` 写普通项目文件不触发 memory index 更新。

## 第一版验收标准

必须满足：

- 删除原 `nano_code/memory.py`，改为 `nano_code/memory/` 包。
- 旧记忆文件可读。
- `/memory` 可用。
- system prompt 中记忆说明可用。
- 写入记忆目录后自动更新索引。
- 召回先本地打分再 sideQuery 精选。
- sideQuery 失败有保守回退。
- 注入受单条和总预算限制。
- consolidation 不硬删除文件。
- 新增 memory 单元测试。
- 现有测试通过。

可以延期：

- 自动会话抽取。
- `/memory maintain --apply` 命令。
- 全局用户记忆。
- embedding 检索。
- YAML/Pydantic/SQLite/LanceDB。
- MCP 或外部服务化。

## 设计取舍

这次重构的关键取舍是：

```text
吸收 SimpleMem 的记忆质量和召回思想
不吸收 SimpleMem 的重基础设施
```

原因是 `nano_code` 是一个轻量 coding agent。它需要长期记忆帮它跨会话保留用户偏好、项目约束和外部参考，但不需要一开始就变成完整记忆平台。

先用文件式结构化记忆把边界做稳，后续如果记忆规模、召回质量或多客户端共享真的成为瓶颈，再逐步引入 SQLite、embedding、MCP 等能力。这样每一步都有明确收益，也不会牺牲当前项目的简洁性和可维护性。
