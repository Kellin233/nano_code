# Tool 模块重构方案

## 目标

把当前 `src/tools.py` 从“工具定义、内置工具执行、权限、延迟加载、结果截断全部混在一个文件里”的结构，重构成职责清晰、边界稳定、便于测试和扩展的工具模块。

本轮允许调整 `tool` 以外的少量调用点，也允许改变内部和对外 import API。要求是：功能行为不变，模块边界更清楚，后续新增工具、接入 MCP、调整权限策略时不需要继续堆大文件。

这里的“不改变其他功能实现”指不改变用户可感知行为和既有运行语义，不是指 import 路径、函数名、文件结构完全不动。

本轮重构重点：

- 工具 schema 和工具实现分离。
- 内置工具执行和状态型工具路由分离。
- 权限判断集中，不散落到具体工具里。
- 工具 registry 接管工具集合、deferred 激活、MCP 工具定义合并。
- Agent 层只负责会话状态、确认 UI、skill、sub-agent、MCP 调用路由。
- MCP 融合进工具目录和权限模型，但不把 MCP 连接生命周期塞进内置工具执行层。

## 总体设计

### 结论

删除单文件：

```text
src/tools.py
```

改成包：

```text
src/tools/
├── __init__.py
├── types.py
├── definitions.py
├── builtin.py
├── permissions.py
├── registry.py
└── runtime.py
```

同时适度修改这些调用点：

```text
src/agent/core.py
src/agent/backends.py
src/agent/tools_runtime.py
src/agent/models.py
src/agent/__init__.py
src/prompt.py
src/subagent.py
src/mcp_client.py   # 只在需要补 metadata/sanitize 时改
nanocode/test/*.py
```

模块职责：

| 模块 | 职责 |
|------|------|
| `tools/__init__.py` | 工具包公共出口，只导出稳定、必要的公共 API |
| `tools/types.py` | 工具定义类型、权限结果、工具来源等轻量类型 |
| `tools/definitions.py` | 内置工具 schema、内置工具分组常量 |
| `tools/builtin.py` | 内置工具具体实现：文件、搜索、shell、web_fetch |
| `tools/permissions.py` | 权限规则、危险命令识别、settings 加载、权限裁决 |
| `tools/registry.py` | 工具目录：内置/MCP/custom 工具合并、deferred 激活、schema 清理、并发安全查询 |
| `tools/runtime.py` | 内置工具执行入口：read-before-edit、mtime 检查、结果截断、handler 分发 |

`tool` 模块负责“工具系统通用能力”，不负责“Agent 当前会话状态”。

tool 模块包含：

- 工具定义。
- 内置工具实现。
- 工具权限规则。
- 工具 registry。
- deferred 工具激活。
- read-before-edit 和 mtime 保护。
- 工具结果截断。
- MCP 工具定义的接入和 metadata 管理。

tool 模块不包含：

- 当前模型。
- 消息历史。
- token 统计。
- 用户确认 UI。
- skill 激活状态。
- sub-agent 创建。
- MCP server 连接生命周期。
- MCP 外部进程管理。

### 为什么改成 `tools/` 包

之前保守方案是新增 `tooling/` 包，同时保留 `tools.py` 作为兼容门面。现在既然允许 API 调整，就不应该继续让历史文件名决定新设计。

直接改成 `nanocode.domains.tools` 包有几个好处：

- 包名符合领域概念，不需要额外解释 `tooling`。
- 调用方可以按职责导入，例如 `nanocode.domains.tools.permissions`、`nanocode.domains.tools.runtime`。
- `__init__.py` 可以只导出少量公共 API，不需要继续 re-export 私有函数。
- 后续新增模块不会让 `tools.py` 重新膨胀。

注意：Python 同一路径下不能同时存在 `tools.py` 和 `tools/`。实施时必须先删除或迁移 `tools.py`，再创建 `tools/` 包。

## MCP 融合边界

MCP 可以融合进工具系统，但融合的是“工具定义、工具 metadata、权限和 registry”，不是 MCP 连接和执行生命周期。

保留边界：

```text
tools.registry
  - 接收 MCP tool definitions
  - 加 origin/mcp metadata
  - 合并工具列表
  - 对模型输出 sanitized schema
  - 告诉 backend 某个工具是否 concurrency safe

mcp_client.py
  - 读取 MCP 配置
  - 连接 server
  - tools/list
  - tools/call
  - 断开和清理资源

agent/tools_runtime.py
  - 判断 name 是否 MCP 工具
  - 调 McpManager.call_tool()
```

不要把 `McpManager` 放进 `tools/runtime.py`。MCP 工具有连接状态、外部进程、异步调用、初始化失败、断连恢复，这些都不是内置工具执行层应该承担的职责。

MCP 工具第一轮默认策略：

- 可以进入 `ToolRegistry`。
- 可以出现在模型工具 schema 中。
- 默认不是 read-only。
- 默认不是 concurrency safe。
- 默认不通过 `tool_search` 延迟搜索，除非后续明确设计。
- 具体调用仍走 `McpManager.call_tool()`。

后续如果 MCP 工具支持只读 metadata，可以扩展：

```python
registry.add_many(
    mcp_defs,
    origin="mcp",
    default_concurrency_safe=False,
)
```

但不要让 MCP 工具默认享受内置只读工具的并发和自动放行策略。

## 详细设计

### 1. `tools/types.py`

第一版保持轻量，不引入 Pydantic 或复杂泛型。

建议：

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ToolDef = dict[str, Any]
PermissionMode = Literal["default", "acceptEdits", "bypassPermissions", "dontAsk"]
PermissionAction = Literal["allow", "deny", "confirm"]
ToolOrigin = Literal["builtin", "mcp", "custom"]


@dataclass(frozen=True)
class PermissionDecision:
    action: PermissionAction
    message: str = ""

    def as_dict(self) -> dict[str, str]:
        result = {"action": self.action}
        if self.message:
            result["message"] = self.message
        return result


@dataclass
class ToolMetadata:
    name: str
    origin: ToolOrigin = "builtin"
    deferred: bool = False
    concurrency_safe: bool = False
    read_only: bool = False
    edit_tool: bool = False
    raw: dict[str, Any] = field(default_factory=dict)
```

说明：

- `ToolDef` 仍是 dict，避免影响 Anthropic/OpenAI schema。
- `PermissionDecision` 可以让权限模块内部更清楚，但为了减少调用点改动，也可以让 `check_permission()` 继续返回 dict。两种做法二选一。
- 如果返回 `PermissionDecision`，需要同步改 `agent/backends.py`。这是允许的，但要保持行为文案不变。
- `ToolMetadata` 是 registry 内部信息，不直接发给模型 API。

第一轮不要做复杂 `Tool` protocol。当前内置工具契约是 `dict -> str`，够用。

### 2. `tools/definitions.py`

本模块放内置工具 schema 和内置工具分类常量。

建议：

```python
READ_TOOL_NAMES = {"read_file", "list_files", "grep_search", "web_fetch"}
EDIT_TOOL_NAMES = {"write_file", "edit_file"}
CONCURRENCY_SAFE_BUILTIN_TOOLS = {"read_file", "list_files", "grep_search", "web_fetch"}

BUILTIN_TOOL_DEFINITIONS: list[ToolDef] = [...]


def builtin_tool_definitions() -> list[ToolDef]:
    return [dict(tool) for tool in BUILTIN_TOOL_DEFINITIONS]
```

要求：

- 内置工具 schema 的 `name`、`description`、`input_schema`、`required` 保持不变。
- `agent`、`skill` 仍只放 schema，不在 definitions 执行。
- `tool_search` 仍作为工具 schema 暴露。
- 返回 list 时避免调用方原地修改全局 schema。

`tool_definitions` 这个旧名字可以不保留。调用方统一改为：

```python
from nanocode.domains.tools.definitions import builtin_tool_definitions
```

或者通过 registry：

```python
registry = ToolRegistry.with_builtin_tools()
```

### 3. `tools/builtin.py`

本模块放所有本地内置工具实现。

包含：

- `read_file()`
- `write_file()`
- `edit_file()`
- `list_files()`
- `grep_search()`
- `run_shell()`
- `web_fetch()`

辅助函数：

- `_auto_update_memory_index()`
- `_normalize_quotes()`
- `_find_actual_string()`
- `_generate_diff()`
- `_grep_python()`

可以把原来的私有工具函数改成不带下划线的模块内公共函数，因为它们现在处在明确的 `tools.builtin` 命名空间里：

```python
from nanocode.domains.tools.builtin import write_file
```

但不建议从 `tools/__init__.py` 导出这些具体工具函数。外部业务通常不应该直接调用某个工具实现，而应该通过 `execute_builtin_tool()` 或 Agent 路由。

`write_file()` 必须保留 memory index 更新：

```python
def _auto_update_memory_index(file_path: str) -> None:
    try:
        sync_memory_file(Path(file_path))
    except Exception:
        pass
```

这个 best-effort 副作用属于现有行为，不能因为拆模块而丢掉。

`edit_file()` 继续使用 search-and-replace：

- `old_string` 不存在时报错。
- 匹配多次时报错。
- 支持 quote normalization。
- 返回简化 diff。

不要改成 line edit、AST edit、unified diff 或全文件重写。

### 4. `tools/permissions.py`

本模块负责权限裁决，不执行工具。

放入：

- `DANGEROUS_PATTERNS`
- `is_dangerous()`
- `_parse_rule()`
- `_load_settings()`
- `load_permission_rules()`
- `_matches_rule()`
- `_check_permission_rules()`
- `check_permission()`
- `reset_permission_cache()`

建议新签名：

```python
def check_permission(
    tool_name: str,
    inp: dict,
    *,
    mode: PermissionMode = "default",
    metadata: ToolMetadata | None = None,
) -> PermissionDecision:
```

如果为了减少迁移量，也可以第一轮继续返回 dict。但新设计不需要保留 `plan_file_path` 参数，因为当前代码已经没有实际 Plan Mode 分支，且本轮允许 API 改动。

权限语义保持：

1. `bypassPermissions` 直接允许。
2. settings deny 优先于 allow。
3. read-only 工具默认允许。
4. `acceptEdits` 允许 edit 工具。
5. 危险 shell、新建文件、编辑不存在文件需要 confirm。
6. `dontAsk` 对 confirm 项自动 deny。
7. 其他情况 allow。

read-only 判断来源：

- 内置工具通过 `metadata.read_only` 或 `READ_TOOL_NAMES`。
- MCP 工具默认不是 read-only。
- custom 工具默认不是 read-only。

settings 规则继续支持当前格式：

```json
{
  "permissions": {
    "allow": ["run_shell(npm test*)"],
    "deny": ["run_shell(rm*)"]
  }
}
```

第一轮不扩展 settings schema，避免把工具重构变成权限配置重构。

### 5. `tools/registry.py`

这是本轮最重要的抽象。

当前 `_activated_tools` 是 `tools.py` 的全局状态。更合理的设计是让每个 Agent 拥有自己的 `ToolRegistry`，避免不同 Agent 或测试之间互相污染。

建议：

```python
class ToolRegistry:
    def __init__(self, tools: list[ToolDef] | None = None):
        self._tools: dict[str, ToolDef] = {}
        self._metadata: dict[str, ToolMetadata] = {}
        self._activated_deferred: set[str] = set()
        if tools:
            self.add_many(tools, origin="builtin")

    @classmethod
    def with_builtin_tools(cls) -> "ToolRegistry":
        registry = cls()
        registry.add_many(builtin_tool_definitions(), origin="builtin")
        return registry

    def add_many(
        self,
        tools: list[ToolDef],
        *,
        origin: ToolOrigin = "custom",
        default_concurrency_safe: bool = False,
    ) -> None:
        ...

    def active_definitions(self, denied: set[str] | None = None) -> list[ToolDef]:
        ...

    def deferred_names(self, denied: set[str] | None = None) -> list[str]:
        ...

    def search_deferred(self, query: str) -> list[ToolDef]:
        ...

    def metadata_for(self, name: str) -> ToolMetadata | None:
        ...

    def is_concurrency_safe(self, name: str, inp: dict | None = None) -> bool:
        ...

    def names(self) -> set[str]:
        ...
```

registry 职责：

- 合并内置工具、MCP 工具、custom tools。
- 去重。
- 记录 origin。
- 记录 deferred 激活状态。
- 输出给模型前清理内部字段。
- 给 backend 查询并发安全。
- 给权限模块提供 metadata。

合并策略：

- 保持插入顺序。
- 同名工具保留先注册的。
- 内置工具先注册。
- MCP 工具后注册。
- custom tools 由 Agent 或 sub-agent 显式传入。

不要全局排序。工具顺序会影响模型工具选择和 prompt cache。

schema 清理：

```python
INTERNAL_SCHEMA_KEYS = {"deferred", "origin", "concurrency_safe", "read_only", "edit_tool"}


def sanitize_tool_definition(tool: ToolDef) -> ToolDef:
    return {k: v for k, v in tool.items() if k not in INTERNAL_SCHEMA_KEYS}
```

`search_deferred()` 返回值用于 `tool_search` 工具结果，必须返回 sanitized schema。

### 6. `tools/runtime.py`

本模块只执行内置工具。

建议命名：

```python
async def execute_builtin_tool(
    name: str,
    inp: dict,
    read_file_state: dict[str, float] | None = None,
) -> str:
    ...
```

不要继续叫 `execute_tool()`，因为这个名字容易让人误以为它能执行 skill、agent、MCP。现在要让名字表达边界。

执行流程：

```text
1. read_file 特殊处理：执行后记录 mtime。
2. write_file/edit_file 前做 read-before-edit 和 mtime 检查。
3. 根据 handler map 执行内置工具。
4. 截断过长结果。
5. 写入/编辑成功后更新 read_file_state。
```

`tool_search` 不放在 `runtime.py` 执行。它是 registry 操作，不是内置文件/shell/web 工具。

因此 `agent/tools_runtime.py` 应该先处理：

```python
if name == "tool_search":
    return self._execute_tool_search(inp)
```

或者：

```python
if name == "tool_search":
    return self._tool_registry.search_deferred_text(inp.get("query", ""))
```

这样 deferred 激活状态由 Agent 自己的 registry 管理，而不是全局变量。

handler map：

```python
BUILTIN_HANDLERS = {
    "write_file": write_file,
    "edit_file": edit_file,
    "list_files": list_files,
    "grep_search": grep_search,
    "run_shell": run_shell,
    "web_fetch": web_fetch,
}
```

`read_file` 仍单独处理，因为它需要更新 `read_file_state`。

### 7. `tools/__init__.py`

`__init__.py` 只导出真正稳定的公共 API，不再 re-export 私有实现函数。

建议：

```python
from .definitions import builtin_tool_definitions
from .permissions import check_permission, reset_permission_cache
from .registry import ToolRegistry
from .runtime import execute_builtin_tool
from .types import PermissionDecision, PermissionMode, ToolDef, ToolMetadata

__all__ = [
    "PermissionDecision",
    "PermissionMode",
    "ToolDef",
    "ToolMetadata",
    "ToolRegistry",
    "builtin_tool_definitions",
    "check_permission",
    "execute_builtin_tool",
    "reset_permission_cache",
]
```

旧的 `_write_file`、`_edit_file` 等不再从 `nanocode.domains.tools` 导出。测试改成：

```python
from nanocode.domains.tools.builtin import write_file
```

这是一次明确的 API 清理，不需要兼容旧私有导入。

## Agent 侧改动

### 1. `agent/core.py`

Agent 初始化时创建 registry：

```python
from ..tools import ToolRegistry
from ..tools.definitions import builtin_tool_definitions

...
base_tools = custom_tools if custom_tools is not None else builtin_tool_definitions()
self._tool_registry = ToolRegistry(base_tools)
```

如果仍保留 `self.tools`，它容易和 registry 状态分叉。建议迁移后删除 `self.tools`，统一通过 `self._tool_registry` 获取工具定义。

MCP 懒初始化：

```python
await self._mcp_manager.load_and_connect()
mcp_defs = self._mcp_manager.get_tool_definitions()
if mcp_defs:
    self._tool_registry.add_many(
        mcp_defs,
        origin="mcp",
        default_concurrency_safe=False,
    )
```

不要把 MCP manager 传给 registry。registry 只保存定义和 metadata，不负责连接。

系统提示词构建：

当前 `build_system_prompt()` 自己调用全局 `get_deferred_tool_names()`。迁移后应改成 Agent 传入：

```python
self._base_system_prompt = custom_system_prompt or build_system_prompt(
    deferred_tool_names=self._tool_registry.deferred_names()
)
```

这样 deferred 状态和工具集属于当前 Agent，不再依赖全局工具状态。

### 2. `agent/tools_runtime.py`

当前 `_current_tool_definitions()` 从 `self.tools` 过滤 active skill denied tools。迁移后：

```python
def _current_tool_definitions(self) -> list[ToolDef]:
    denied = self._active_skills.disallowed_tools()
    return self._tool_registry.active_definitions(denied=denied)
```

工具执行路由：

```python
async def _execute_tool_call(self, name: str, inp: dict) -> str:
    if name == "agent":
        return await self._execute_agent_tool(inp)
    if name == "skill":
        return await self._execute_skill_tool(inp)
    if name == "tool_search":
        return self._execute_tool_search(inp)
    if self._mcp_manager.is_mcp_tool(name):
        return await self._mcp_manager.call_tool(name, inp)
    return await execute_builtin_tool(name, inp, self._read_file_state)
```

`tool_search`：

```python
def _execute_tool_search(self, inp: dict) -> str:
    matches = self._tool_registry.search_deferred(inp.get("query", ""))
    if not matches:
        return "No matching deferred tools found."
    return json.dumps(matches, indent=2)
```

skill/sub-agent 过滤工具：

- 如果 `SkillInvocationResult.allowed_tools` 存在，用 registry 过滤 names。
- 如果 `disallowed_tools` 存在，用 registry active definitions 的 denied 参数处理。
- 子 Agent 创建时传入 filtered tool definitions，让子 Agent 构造自己的 registry。

### 3. `agent/backends.py`

不要继续直接 import `CONCURRENCY_SAFE_TOOLS`。改成查询 registry：

```python
if self._tool_registry.is_concurrency_safe(block["name"], block["input"]):
    ...
```

权限检查改成带 metadata：

```python
metadata = self._tool_registry.metadata_for(tool_name)
perm = check_permission(
    tool_name,
    inp,
    mode=self.permission_mode,
    metadata=metadata,
)
```

如果 `check_permission()` 返回 `PermissionDecision`，backend 代码应改成属性访问：

```python
if perm.action == "deny":
    ...
if perm.action == "confirm" and perm.message:
    ...
```

用户可见的拒绝和确认文案保持不变。

### 4. `prompt.py`

`build_system_prompt()` 改成接收 deferred names：

```python
def build_system_prompt(deferred_tool_names: list[str] | None = None) -> str:
    deferred_names = deferred_tool_names or []
    ...
```

这样 prompt 不直接 import tool registry，也不依赖全局 activated tools。

### 5. `subagent.py`

从：

```python
from .tools import tool_definitions, ToolDef
```

改成：

```python
from .tools.definitions import builtin_tool_definitions
from .tools.types import ToolDef
```

`get_sub_agent_config()` 中每次使用新的 list：

```python
tools = builtin_tool_definitions()
```

避免多个 Agent 共享同一个可变 list。

### 6. `agent/models.py` 和 `agent/__init__.py`

`agent/models.py` 只需要 `ToolDef`，改成：

```python
from ..tools.types import ToolDef
```

`agent/__init__.py` 是兼容出口，但本轮既然允许 API 改动，不需要继续 re-export 工具运行函数。建议只保留 Agent 相关导出，删除工具层旧兼容导出。

如果测试或用户代码需要工具 API，应显式从 `nanocode.domains.tools` 或具体子模块导入。

## 硬性约束

### 行为不变，API 可以变

允许改变：

- import 路径。
- 函数名，例如 `execute_tool()` 改成 `execute_builtin_tool()`。
- `tools.py` 单文件改为 `tools/` 包。
- 私有函数导出方式。
- Agent 内部字段，例如 `self.tools` 改为 `self._tool_registry`。

不能改变：

- 用户使用 nanocode 的主要行为。
- 工具 schema 的语义。
- 工具执行结果的关键文案。
- 权限模式语义。
- skill、sub-agent、MCP、memory、session 的既有运行行为。

### 工具行为保持不变

不能改变：

- read 文件的行号格式。
- write 文件后返回前 30 行预览。
- edit 文件必须唯一匹配。
- quote normalization。
- grep 的系统命令优先、Python fallback。
- shell timeout 单位是毫秒。
- web_fetch 30 秒 timeout、HTML 简单去标签、`max_length`。
- 工具层 50K 字符截断。
- Agent 层 30KB 大结果持久化。
- `Unknown tool`、`No matches found`、`No matching deferred tools found` 等关键返回语义。

### 权限行为保持不变

不能改变：

- `bypassPermissions` 直接允许。
- deny 规则优先于 allow。
- read-only 工具默认允许。
- `acceptEdits` 允许编辑工具。
- `dontAsk` 自动拒绝 confirm 项。
- 危险 shell 需要确认。
- 新建文件需要确认。
- 编辑不存在文件需要确认。

### 并发安全保持 fail-closed

内置并发安全工具仍是：

```python
{"read_file", "list_files", "grep_search", "web_fetch"}
```

MCP/custom 工具默认不并发安全。

如果未来给 MCP 工具加并发安全能力，必须显式 metadata 或白名单，不允许默认推断。

### 不引入重依赖

第一版不新增第三方依赖。

不引入：

- Pydantic。
- requests。
- pluggy。
- dependency injection framework。
- 复杂插件框架。

当前项目规模下，轻量 dataclass、dict schema、明确模块边界已经足够。

### 不扩大功能范围

本轮重构不是新增工具，不是重做 MCP，不是重做权限系统。

不做：

- 新增 Plan Mode。
- 删除或恢复 Plan Mode。
- 新增 MCP 权限配置格式。
- 新增 tool hook。
- 新增 sandbox executor。
- 新增 UI 交互。

## 隐含要求

### registry 是 Agent 级状态

deferred 激活状态不能继续做成全局变量。每个 Agent 应拥有自己的 `ToolRegistry`。

原因：

- 主 Agent、sub-agent、skill fork 的工具集可能不同。
- 测试不应该依赖全局 reset。
- MCP 工具可能只在主 Agent 注册。
- active skill 可能动态隐藏部分工具。

### `tool_search` 是 registry 操作

`tool_search` 不应该放在内置工具 handler map 里。它不读文件、不跑 shell、不访问网络，它只是让 registry 激活 deferred schema。

这能避免 `execute_builtin_tool()` 需要知道 registry 状态。

### read-before-edit 仍属于工具 runtime

`read_file_state` 是 Agent 会话状态，但 read-before-edit 是工具执行通用保护。

因此边界是：

- Agent 持有 `self._read_file_state`。
- `execute_builtin_tool()` 接收这个 dict 并执行检查。

不要把 read-before-edit 下沉到 `builtin.write_file()` 或 `builtin.edit_file()`。

### 权限检查仍在执行前

backend 仍然先做权限检查，再执行工具。

工具实现不应该自己决定是否允许执行。否则权限规则会散落在多个函数里，后续很难保证所有工具都遵守同一套策略。

### memory index 更新是 write_file 行为

`write_file()` 仍然 best-effort 调 `sync_memory_file()`。

这不是工具重构可以删除的副作用。长期记忆索引更新依赖它。

### MCP 是外部工具来源

MCP 工具可以进入 registry，也可以参与权限和并发安全判断。但 MCP 工具不是 builtin tool，不进入 `execute_builtin_tool()`。

## 不能做什么

- 不能保留 `tools.py` 同时新增 `tools/` 包。
- 不能为了省事继续把所有逻辑堆在 `tools/__init__.py`。
- 不能把所有工具抽象成复杂类层级。
- 不能让 `tools.runtime` import `Agent`。
- 不能让 `tools.registry` 连接 MCP server。
- 不能把 `agent`、`skill`、MCP 都塞进 `execute_builtin_tool()`。
- 不能让 MCP/custom 工具默认 read-only 或 concurrency safe。
- 不能把权限判断分散进每个工具实现。
- 不能把 shell 危险命令判断放进 `run_shell()` 后才做。
- 不能把 search-and-replace 编辑改成 diff、AST 或行号编辑。
- 不能为了“统一格式”修改工具返回文案。
- 不能把 `sync_memory_file()` 异常暴露给普通写文件。
- 不能把 schema 内部字段发给模型 API。
- 不能全局排序工具列表。
- 不能顺手重写 prompt、UI、session、MCP 协议。

## 可能踩坑的地方

### 1. 文件到包的迁移

必须删除：

```text
src/tools.py
```

再创建：

```text
src/tools/
```

如果两者同时存在，Python import 会出问题。实施时建议一次提交内完成迁移，避免中间状态。

### 2. 旧私有函数导入

现有测试里有：

```python
from nanocode.domains.tools import _write_file
```

本轮允许 API 清理，应改成：

```python
from nanocode.domains.tools.builtin import write_file
```

不要为了兼容私有函数，把 `tools/__init__.py` 又变成大杂烩。

### 3. registry 和 custom tools

`Agent(custom_tools=...)` 当前直接设置 `self.tools`。迁移后必须确保 custom tools 进入 registry。

子 Agent、skill fork、自定义 agent allowed-tools 都依赖这个入口。

### 4. prompt 构建时机

当前 `build_system_prompt()` 在 Agent 初始化时调用，并从全局工具状态读取 deferred names。

迁移后 deferred names 来自 Agent registry，所以 Agent 初始化顺序要调整：

```text
先创建 registry
再 build_system_prompt(deferred_tool_names=...)
再初始化后端消息历史
```

OpenAI-compatible 后端的 system message 也要使用更新后的 prompt。

### 5. active skill 隐藏工具

`_current_tool_definitions()` 现在需要同时考虑：

- registry active definitions。
- active skills disallowed tools。
- deferred 工具未激活。

过滤顺序建议：

```text
registry 根据 deferred 取 active
再按 denied names 过滤
最后 sanitize schema
```

或者 registry 接收 denied 参数统一处理。不要在多个调用点重复过滤。

### 6. 并发安全判断

`agent/backends.py` 现在直接看 `CONCURRENCY_SAFE_TOOLS`。迁移后要改成 registry 查询。

否则 MCP/custom 工具 metadata 无法生效，也会继续把并发策略散在工具定义外面。

### 7. 权限 metadata 缺失

如果 `metadata_for(name)` 返回 None，权限系统必须 fail-closed：

- 不认为它 read-only。
- 不认为它 edit tool。
- shell 危险检查仍按 name == `run_shell` 生效。

### 8. schema 内部字段泄漏

registry 可能给工具定义加：

- `origin`
- `deferred`
- `concurrency_safe`
- `read_only`
- `edit_tool`

这些不能发给 Anthropic/OpenAI。`active_definitions()` 和 `search_deferred()` 必须统一 sanitize。

### 9. 工具顺序

工具顺序不要随意改变。

建议：

```text
builtin tools 按原顺序
custom tools 按传入顺序
MCP tools 按 MCP server 返回顺序追加
```

不要全局排序。

### 10. read_file_state 路径一致性

读、写、编辑必须继续使用：

```python
str(Path(file_path).resolve())
```

作为状态 key。不要一个地方用相对路径，一个地方用绝对路径。

### 11. mtime 精确比较

当前用 `os.path.getmtime()` 精确比较。不要顺手改成整数秒、文件 hash 或更复杂机制。

如果后续要优化外部修改检测，应单独设计。

### 12. grep 平台差异

非 Windows 优先系统 `grep`，Windows 或失败时 Python fallback。两者输出略有差异。

测试不要对路径格式、排序做过细断言，只验证核心行为。

### 13. MCP 初始化失败

MCP 初始化失败不能影响普通内置工具。registry 不能强依赖 MCP 连接成功。

Agent 仍应 best-effort 初始化 MCP：失败只打印信息，继续会话。

### 14. 两层结果保护

保留两层：

- `execute_builtin_tool()` 50K 字符截断。
- `AgentToolRuntimeMixin._persist_large_result()` 30KB 落盘预览。

前者保护工具直接结果，后者保护消息历史和上下文。

## 实现顺序

### 第一阶段：补行为锁测试

新增或调整 `nanocode/test/test_tools.py`，覆盖：

1. 权限：
   - read-only 工具默认 allow。
   - dangerous shell confirm。
   - dangerous shell 在 `dontAsk` 下 deny。
   - deny 规则优先 allow。
   - `acceptEdits` 允许 edit tools。
   - unknown metadata fail-closed。

2. registry：
   - builtin tools 按原顺序输出。
   - deferred 未激活时不出现在 active definitions。
   - `tool_search` 激活 deferred。
   - schema sanitize 不泄漏内部字段。
   - registry 实例之间 deferred 状态互不影响。
   - MCP/custom 工具默认不 concurrency safe。

3. runtime：
   - 未 read 已存在文件时 write/edit 被拒绝。
   - read 后 write/edit 允许。
   - read 后外部修改再 edit 返回 warning。
   - unknown builtin tool 返回 `Unknown tool`。
   - 结果超过上限会截断。

4. edit：
   - old_string 不存在时报错。
   - old_string 多次出现时报错。
   - quote normalization 可匹配。

5. memory index：
   - `write_file()` 仍更新 active memory index。

测试先锁行为，再迁移模块，避免重构过程中误改语义。

### 第二阶段：创建 `tools/` 包并迁移代码

步骤：

1. 删除 `src/tools.py`。
2. 新建 `src/tools/`。
3. 创建 `types.py`。
4. 创建 `definitions.py`，迁移 schema 和常量。
5. 创建 `builtin.py`，迁移内置工具实现。
6. 创建 `permissions.py`，迁移权限逻辑。
7. 创建 `registry.py`，实现 `ToolRegistry`。
8. 创建 `runtime.py`，实现 `execute_builtin_tool()`。
9. 创建 `__init__.py`，只导出稳定公共 API。

迁移时尽量保持函数体不变，先完成结构拆分，再做小范围命名清理。

每完成一个阶段跑：

```bash
python -m compileall src test
```

### 第三阶段：迁移 Agent 调用点

修改：

- `agent/core.py`
- `agent/tools_runtime.py`
- `agent/backends.py`
- `agent/models.py`
- `agent/__init__.py`

目标：

- Agent 持有 `self._tool_registry`。
- 删除或停止使用 `self.tools`。
- `_current_tool_definitions()` 从 registry 获取。
- `tool_search` 走 registry。
- builtin 工具调用 `execute_builtin_tool()`。
- MCP 调用仍走 `McpManager.call_tool()`。
- 并发安全判断走 registry。
- 权限检查带 metadata。

### 第四阶段：迁移 prompt 和 subagent

修改 `prompt.py`：

- `build_system_prompt()` 接收 `deferred_tool_names`。
- 不再直接 import 全局 `get_deferred_tool_names()`。

修改 `subagent.py`：

- 使用 `builtin_tool_definitions()`。
- 自定义 agent allowed-tools 过滤新的 list。
- 子 Agent 通过 custom tools 初始化自己的 registry。

### 第五阶段：迁移测试和旧 import

用 `rg` 找旧导入：

```bash
rg -n "from \\.tools import|from \\.\\.tools import|from nanocode\\.tools import|import nanocode\\.tools" nanocode test
```

按职责改成：

```python
from nanocode.domains.tools.types import ToolDef
from nanocode.domains.tools.definitions import builtin_tool_definitions
from nanocode.domains.tools.permissions import check_permission
from nanocode.domains.tools.runtime import execute_builtin_tool
from nanocode.domains.tools.registry import ToolRegistry
from nanocode.domains.tools.builtin import write_file
```

不要为了省事让 `tools/__init__.py` 导出所有旧名字。

### 第六阶段：验证

运行：

```bash
python -m compileall src test
python -m unittest discover nanocode/test
```

手工验收：

1. `read_file` 返回带行号内容。
2. 未读已有文件直接 `edit_file` 被拒绝。
3. 读后外部修改，再 edit 返回 warning。
4. `write_file` 新建文件在 default 权限下触发 confirm。
5. `run_shell` 危险命令在 default 权限下触发 confirm。
6. OpenAI-compatible 后端连续只读工具仍能并发。
7. Anthropic 流式 block 完成后只读工具仍能提前执行。
8. MCP server 可连接时，MCP 工具出现在工具列表并可调用。
9. MCP 初始化失败时，普通内置工具仍可用。
10. skill 和 sub-agent 仍按原逻辑过滤工具。

### 第七阶段：文档和清理

可以更新：

- `docs/02-tools.md` 的模块说明。
- `docs/15-code-reading-guide.md` 的工具入口说明。
- 测试文档中旧 `tools.py` 路径。

不要在本轮大规模改 prompt 文案或教程内容。文档更新以路径和架构说明为主。

## 扩展方向

### 新增内置工具

新增工具时：

1. 在 `definitions.py` 添加 schema。
2. 在 `builtin.py` 添加实现。
3. 在 `runtime.py` handler map 注册。
4. 在 registry metadata 中标注 read-only/edit/concurrency。
5. 在 `permissions.py` 补特殊权限规则，如果需要。
6. 补测试。

### MCP 权限增强

后续可以支持 MCP 工具 metadata：

- read-only。
- destructive。
- requires confirmation。
- concurrency safe。

但必须显式配置或由 MCP server 明确声明，不能默认推断。

### ToolRegistry 演进

第一版 `ToolRegistry` 保持简单。

如果后续工具来源增加，比如 plugin、hook 生成工具、用户自定义工具，再扩展：

```python
class ToolProvider(Protocol):
    def list_tools(self) -> list[ToolDef]: ...
```

当前不要提前引入 provider 抽象。

### 工具结果结构化

当前工具返回 string。后续如果要支持 `is_error`、artifact、structured content，可以新增：

```python
@dataclass
class ToolResult:
    content: str
    is_error: bool = False
```

但这会影响 Agent 后端回灌格式，应单独设计。本轮不做。

## 最终结构预期

模型工具调用路径：

```text
agent/backends.py
  - 解析 tool call
  - registry 判断 concurrency safe
  - check_permission(metadata)
  - 串行/并发调度
        |
        v
agent/tools_runtime.py
  - agent tool
  - skill tool
  - tool_search registry operation
  - MCP tool
  - builtin tool
        |
        v
tools/runtime.py
  - execute_builtin_tool()
  - read-before-edit
  - mtime check
  - truncate
        |
        v
tools/builtin.py
  - read/write/edit/list/grep/shell/web_fetch
```

MCP 工具路径：

```text
agent/core.py
  -> McpManager.load_and_connect()
  -> McpManager.get_tool_definitions()
  -> ToolRegistry.add_many(origin="mcp")
  -> registry.active_definitions()
  -> 模型可见 MCP schema

agent/tools_runtime.py
  -> McpManager.call_tool()
```

设计结果：

- `tools` 是真正的包，不再是单文件大杂烩。
- Agent 拥有自己的 ToolRegistry，没有全局 deferred 状态污染。
- 内置工具、MCP 工具、skill/sub-agent 路由边界清晰。
- 权限和并发安全判断可以基于 metadata 扩展。
- 代码风格保持函数式、轻量 dataclass、少量必要类，不引入炫技式框架。
- 后续新增工具或接入更多工具来源时，有明确落点。

## 取舍说明

本方案没有追求“所有工具都是类”“所有工具都走 provider 插件框架”。当前项目还不需要那么重。

本方案选择的抽象只有一个核心类：`ToolRegistry`。它是合理的，因为 deferred 状态、MCP/custom 工具合并、metadata 查询天然需要一个有状态对象，而且这个状态应该属于 Agent，而不是全局模块。

其他部分保持简单函数：

- `builtin.py` 是 `dict -> str`。
- `permissions.py` 是纯裁决。
- `runtime.py` 是执行编排。

这符合当前阶段的工程取舍：边界清楚，代码务实，扩展点够用，但不提前搭复杂框架。
