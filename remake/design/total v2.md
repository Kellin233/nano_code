# Nano Code 简历级完善方案 v2

## 目标

把 `nanocode` 从"功能完整但工程粗糙"提升到"面试官挑不出硬伤"的水平。

---

## 优先级 1：Git 历史 + CI/CD

### 1.1 Git 历史重构

**现状**：5 个大 commit，看不出开发过程、设计决策、迭代修复。

```
0328ba2 refactor nanocode runtime architecture   ← 一次性提交所有 runtime 重构
e84d00b Refactor runtime modules and add tests
ba6444e Refactor memory and tool modules
52f40cc Import Nano Code project                 ← 一次性导入全部代码
edd4678 Initial commit
```

**面试官会问**："你怎么开发这个项目的？给我看看 commit 记录。"
"这个 feature 是什么时候加的？为什么这样设计？"

> 5 个大 commit 表明"我写完才提交"，不是"我持续迭代"。面试官会怀疑：你真的理解每一行吗？还是抄的/AI 写的？

**目标状态**：10-15 个语义清晰的 commit，按模块和功能分组，使用 conventional commits 格式。

**实施步骤**：

```bash
# 1. 保留当前工作区为干净状态
git stash  # 如果有未提交的改动

# 2. 用 git rebase -i 拆分第一个大 commit
git rebase -i --root
# 将第一个 commit 标记为 edit，然后：
git reset HEAD^
# 按模块分组提交：
git add src/domains/tools/definitions.py src/domains/tools/types.py src/domains/tools/base.py
git commit -m "feat: add tool type definitions and base contracts"

git add src/domains/tools/builtin.py
git commit -m "feat: implement built-in tools (read/write/edit/list/grep/shell/fetch)"

git add src/domains/tools/registry.py
git commit -m "feat: add ToolRegistry with deferred tool activation"

git add src/domains/tools/runtime.py
git commit -m "feat: add ToolRuntime pipeline with hooks/permissions/large-result-persistence"

git add src/domains/permissions/
git commit -m "feat: implement layered permission system (workspace/rules/shell-safety)"

# ... 依此类推
```

**推荐的 commit 序列**：

| # | 格式 | 内容 |
|---|------|------|
| 1 | `feat: scaffold project structure and CLI entry point` | `__main__.py`, `pyproject.toml` |
| 2 | `feat: add tool type system and base contracts` | `tools/types.py`, `tools/base.py`, `tools/definitions.py` |
| 3 | `feat: implement built-in tools` | `tools/builtin.py` (read/write/edit/list/grep/shell/web_fetch) |
| 4 | `feat: add ToolRegistry with deferred tool activation` | `tools/registry.py` |
| 5 | `feat: add ToolRuntime pipeline with hooks and permissions` | `tools/runtime.py`, `tools/permissions.py` |
| 6 | `feat: implement layered permission system` | `permissions/policy.py`, `rules.py`, `shell.py`, `workspace.py` |
| 7 | `feat: add sandbox subsystem with bwrap/local/microsandbox backends` | `sandbox/` |
| 8 | `feat: implement Agent core with Anthropic/OpenAI streaming backends` | `agent/core.py`, `backends.py`, `models.py` |
| 9 | `feat: add AgentLoop with tool execution and context compression` | `agent/loop.py`, `context.py`, `engine.py` |
| 10 | `feat: implement MCP client with stdio transport and tool routing` | `mcp/` |
| 11 | `feat: add skill discovery, invocation, and fork support` | `skills/` |
| 12 | `feat: implement file-based long-term memory with MEMORY.md index` | `memory/` |
| 13 | `feat: add hook system (PreToolUse/PostToolUse/Stop/UserPromptSubmit)` | `hooks/` |
| 14 | `feat: add TUI with prompt_toolkit and rich renderer` | `tui/` |
| 15 | `feat: add RuntimeThread public API and server mode` | `runtime/thread.py`, `server/` |
| 16 | `feat: add protocol definitions and SDK client` | `protocol/`, `sdk/` |
| 17 | `feat: add capabilities plugin system` | `capabilities/` |
| 18 | `fix: prevent run_shell from falling back to bare subprocess` | `tools/registry.py`, `tools/runtime.py` |
| 19 | `fix: add PreToolUse hook re-validation after input modification` | `tools/runtime.py` |
| 20 | `fix: make sub-agents inherit parent permission_mode` | `agent/tools_runtime.py` |
| 21 | `refactor: centralize magic numbers into constants.py` | `tools/constants.py` + references |
| 22 | `test: add comprehensive test suite` | `test/` |

---

### 1.2 CI/CD Pipeline

**现状**：无。没有自动化质量检查。

**目标**：`.github/workflows/ci.yml` 包含 lint → type-check → test 三步。

**文件**：`.github/workflows/ci.yml`

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.10" }
      - run: pip install ruff
      - run: ruff check src/ test/

  type-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.10" }
      - run: pip install -e . mypy
      - run: mypy src/ --ignore-missing-imports

  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.10" }
      - run: pip install -e .
      - run: pip install anthropic openai prompt_toolkit rich
      - run: python -m compileall src test
      - run: python -m unittest discover -s test -v
      - run: python -m unittest discover -s test/v1 -v
```

---

## 优先级 2：代码规范 + 类型注解

### 2.1 Linter / Formatter 配置

**现状**：`pyproject.toml` 没有 `[tool.ruff]`、`[tool.black]`、`[tool.mypy]`、`[tool.pytest]`。

**面试官会问**："你的代码规范是什么？怎么保证一致性？"

> 没有 linter 配置 = 没有代码规范。团队协作时你怎么保证质量？

**实施**：在 `pyproject.toml` 添加以下配置块：

```toml
[tool.ruff]
target-version = "py310"
line-length = 120
select = [
    "E",   # pycodestyle errors
    "W",   # pycodestyle warnings
    "F",   # pyflakes
    "I",   # isort
    "B",   # flake8-bugbear
    "C4",  # flake8-comprehensions
    "UP",  # pyupgrade
    "SIM", # flake8-simplify
]
ignore = [
    "E501", # line-too-long handled by formatter
]
exclude = ["__pycache__", ".git", ".venv", "venv", "*.egg-info"]

[tool.ruff.format]
quote-style = "double"
indent-style = "space"
skip-magic-trailing-comma = false
line-ending = "auto"

[tool.mypy]
python_version = "3.10"
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = false  # 渐进式：先不强制，逐步补全
ignore_missing_imports = true
exclude = ["test/"]

[tool.pytest.ini_options]
testpaths = ["test"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
addopts = ["-v", "--tb=short"]
```

**执行**：
```bash
pip install ruff mypy pytest
ruff check src/ --fix        # 自动修复所有 lint 问题
ruff format src/             # 统一格式化
mypy src/ --ignore-missing-imports  # 类型检查（预期有一些错误）
```

---

### 2.2 类型注解补全

**现状**：部分函数缺少返回类型注解。核心路径（Agent、ToolRuntime）有注解，但 builtin、permissions、hooks 等模块缺失较多。

**面试官会问**："你用 mypy 了吗？类型注解覆盖率多少？"

> Python 项目不跑类型检查 = 放弃了一项最有效的 bug 预防工具。

**实施策略**：按模块优先级逐步补全。

| 优先级 | 模块 | 原因 |
|-------|------|------|
| 1 | `tools/runtime.py` | 核心执行管线，外部接口多 |
| 1 | `agent/loop.py` | 主循环，最复杂的控制流 |
| 1 | `agent/context.py` | 上下文压缩，复杂状态操作 |
| 2 | `sandbox/manager.py` | 安全关键路径 |
| 2 | `mcp/manager.py` | 外部集成接口 |
| 3 | `permissions/` | 返回类型简单，改动小 |
| 3 | `memory/store.py` | 纯数据操作 |
| 4 | `hooks/` | 接口已定型 |
| 4 | `skills/` | 接口已定型 |

**执行示例**（以 `builtin.py` 为例）：

```python
# 改前
def read_file(inp: dict) -> str:       # ← 缺返回类型
def write_file(inp: dict) -> str:
def grep_search(inp: dict) -> str:

# 改后 (已经正确) ✅
def read_file(inp: dict) -> str:
def write_file(inp: dict) -> str:
def grep_search(inp: dict) -> str:
```

需要重点补全的是模块级别的内部函数。例如 `builtin.py` 中的：
- `_normalize_quotes(s: str) -> str` ✅
- `_find_actual_string(file_content: str, search_string: str) -> str | None` ← 需要加
- `_generate_diff(old_content: str, old_string: str, new_string: str) -> str` ← 需要加

`permissions/rules.py` 中的：
- `_parse_rule(rule: str) -> dict` ← 应该是 `dict[str, str | None]`
- `_matches_tool(rule_tool: str, tool_name: str) -> bool` ← 需要加
- `matches_rule(rule: dict, tool_name: str, inp: dict) -> bool` ← 需要加

---

## 优先级 3：Provider 抽象完成

### 3.1 问题诊断

**现状**：两套 provider 体系并存。

```
旧体系 (实际在使用)                    新体系 (实现了但未使用)
─────────────                         ────────────
runtime/agent/backends.py             providers/anthropic.py
  AgentBackendMixin                     AnthropicProvider ✅ 实现了 ModelProvider 协议
  _call_anthropic_stream()              stream_turn() 
  _call_openai_stream()                 
                                      
runtime/agent/loop.py                 providers/openai_chat.py
  AgentLoop                             OpenAIChatProvider ✅
  _run_anthropic()                      stream_turn()
  _run_openai()                        
                                      
runtime/agent/context.py              core/ports.py
  每个压缩方法 × 2 (Anthropic/OpenAI)    ModelProvider 协议 ✅
                                       ToolExecutor 协议 ✅
                                      
                                      core/turn.py
                                        CoreTurn ✅ 但没人用
```

**面试官会问**：

> "为什么 `providers/anthropic.py` 有一个完整的 `AnthropicProvider`，但主循环用的是 `AgentBackendMixin._call_anthropic_stream`？它们是重复的吗？"

> "`core/ports.py` 定义了 `ModelProvider` 协议，但是 `AgentLoop` 不依赖这个协议，而是直接调用 Agent 的 mixin 方法。为什么定义了这个协议却不用？"

> "我想加 Google Gemini 后端，改多少文件？"

**诚实的回答**（当前）：
> "这是我正在进行的一次重构。目标是把 provider 从 Agent mixin 中解耦出来，让 AgentLoop 依赖抽象的 ModelProvider 协议。但重构还没完成——新代码实现了协议，旧代码还在跑。如果现在加 Gemini，确实要改 5 个文件。"

**目标回答**（完成后）：
> "AgentLoop 依赖 ModelProvider 协议，不关心具体后端。加 Gemini 只需要写一个 `GeminiProvider` 类，实现 `stream_turn()` 方法，然后注册到 provider registry。不需要改 loop、context、backends 中的任何一行。"

---

### 3.2 方案选择

#### 方案 A：删除 providers/ 和 core/（保守，风险低）

```
删除: providers/anthropic.py, providers/openai_chat.py, providers/base.py
      core/ports.py, core/messages.py, core/turn.py
保留: runtime/agent/backends.py (AgentBackendMixin)
      runtime/agent/loop.py (AgentLoop)
      runtime/agent/context.py (双路径压缩)
```

- ✅ 风险低：不动任何实际运行的代码
- ✅ 消除面试时的尴尬问题："为什么有重复代码？"
- ❌ 丧失扩展性：加新 provider 仍然要改 5 个文件
- ❌ 架构没进步

#### 方案 B：完成迁移，让 AgentLoop 使用 ModelProvider 协议（推荐）

```
步骤 1: 让 AnthropicProvider 和 OpenAIChatProvider 与 AgentBackendMixin 行为完全一致
步骤 2: 将 AgentLoop 改为依赖 ModelProvider 协议
步骤 3: 将 context.py 的双路径压缩改为统一消息格式
步骤 4: 删除 AgentBackendMixin 中的 _call_*_stream 方法
步骤 5: provider registry: 根据 config 选择 provider
```

- ✅ 架构正确：解耦 provider 和 loop
- ✅ 扩展性：加新 provider 只需实现一个类
- ✅ 面试亮点："我用 Protocol 实现了 provider 插件化，加新后端只需 1 个文件"
- ❌ 工作量大：涉及 loop.py、backends.py、context.py、core.py 的改动
- ❌ 风险中高：改的是核心循环

**推荐 B**，理由：
- 这是"经得起面试官拷问"的核心项目——Provider 抽象是 LLM agent 框架最关键的架构决策
- 当前有 177 个测试做安全网，改动有保障
- 方案 A 只是"删除死代码"，不算架构改进

---

### 3.3 方案 B 详细步骤

#### 步骤 1：补齐 AnthropicProvider / OpenAIChatProvider

当前 `AnthropicProvider` 缺少的功能（与 `AgentBackendMixin._call_anthropic_stream` 对比）：

| 功能 | AgentBackendMixin | AnthropicProvider | 需补 |
|------|-------------------|-------------------|------|
| streaming 文本输出 | ✅ via `on_text_delta` | ✅ via `ModelTextDelta` | - |
| thinking 块处理 | ✅ 流式输出 + 过滤 | ❌ 未处理 thinking | ✅ |
| 工具调用聚合 | ✅ `tool_blocks_by_index` | ✅ `tool_blocks[event.index]` | - |
| 错误重试 | ✅ `_with_retry` | ❌ 直接调用 | ✅ |
| stream_options | ❌ | ❌ | - |

需要补齐：
1. `AnthropicProvider.stream_turn()` 添加 thinking 块处理（流式输出，最终过滤）
2. `AnthropicProvider.stream_turn()` 包装 `_with_retry`
3. 验证 `OpenAIChatProvider` 的工具调用聚合逻辑与 `_call_openai_stream` 一致

#### 步骤 2：统一消息格式

当前问题：上下文管理（`context.py`）直接操作 `self._anthropic_messages` 和 `self._openai_messages`，格式不同。

解决方案：不改变消息存储格式（改动太大），而是在 `ModelProvider` 层面保持现状。压缩逻辑保持不变（当前工作正常）。

**关键设计决策**：`context.py` 继续维护双格式消息历史。重构只触及"API 调用层"，不触及"上下文管理层"。这样改动范围可控。

#### 步骤 3：改造 AgentLoop

```python
# 改前：AgentLoop 直接调用 Agent mixin 方法
class AgentLoop:
    def __init__(self, agent):
        self.agent = agent
    
    async def _run_anthropic(self, user_message):
        ...
        task = asyncio.create_task(
            agent._call_anthropic_stream(on_text_delta=_text, on_thinking_delta=_text)
        )
        ...

# 改后：AgentLoop 通过 ModelProvider 协议调用
class AgentLoop:
    def __init__(self, agent, model_provider: ModelProvider):
        self.agent = agent
        self._provider = model_provider
    
    async def _run_with_provider(self, user_message):
        ...
        messages = self.agent._get_messages_for_provider()
        async for event in self._provider.stream_turn(messages):
            if isinstance(event, ModelTextDelta):
                yield AssistantTextDelta(event.text)
            elif isinstance(event, ModelTurnComplete):
                # 处理工具调用和 usage
                ...
```

#### 步骤 4：Provider Registry

```python
# src/providers/registry.py (新增)
from .anthropic import AnthropicProvider
from .openai_chat import OpenAIChatProvider
from .base import ProviderConfig
from ..core.ports import ModelProvider

def create_provider(config: ProviderConfig) -> ModelProvider:
    """根据配置创建 provider 实例（可扩展为注册表模式）。"""
    if config.provider == "anthropic":
        return AnthropicProvider(config)
    if config.provider == "openai":
        return OpenAIChatProvider(config)
    raise ValueError(f"Unknown provider: {config.provider}")
```

#### 步骤 5：清理

- `AgentBackendMixin._call_anthropic_stream()` → 删除
- `AgentBackendMixin._call_openai_stream()` → 删除  
- `AgentBackendMixin` → 删除整个 mixin（保留 `_block_to_dict` 为工具函数）
- `AgentLoop._run_anthropic()` / `AgentLoop._run_openai()` → 合并为一个 `_run_with_provider()`

---

### 3.4 风险控制

| 风险 | 缓解措施 |
|------|---------|
| provider 行为不一致导致 Agent 输出异常 | 先写 provider 对比测试，确保新旧输出相同 |
| 改动太大引入 bug | 每次改一个方法，跑全量测试 |
| thinking 块处理遗漏 | 用真实 API (DeepSeek) 验证 thinking 块正确处理 |
| context.py 双路径不受影响 | 明确边界：只改 API 调用层，不动消息历史格式 |

---

## 优先级 4：日志 + 错误处理

### 4.1 日志系统

**现状**：全项目使用 `print()` 输出信息。

```python
# 当前：遍布各处
print(f"[mcp] Connected to '{config.name}' - {len(delta.added)} tools", flush=True)
print(f"[mcp] Failed to connect to '{config.name}': {exc}", flush=True)
```

**面试官会问**："线上出问题怎么排查？有 trace id 吗？日志级别怎么控制？"

> `print()` 在生产环境中无法控制输出级别、无法结构化、无法关联请求。

**实施**：

#### 4.1.1 基础 logging 配置

```python
# src/logging_config.py (新增)
import logging
import sys

def setup_logging(level: int = logging.INFO) -> None:
    """配置 nanocode 全局日志格式。"""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    root = logging.getLogger("nanocode")
    root.setLevel(level)
    root.addHandler(handler)
    # 禁止重复添加
    root.propagate = False

def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"nanocode.{name}")
```

#### 4.1.2 各模块日志替换

| 模块 | 当前 | 改后 |
|------|------|------|
| `mcp/manager.py` | `print(f"[mcp] Connected to ...")` | `logger.info("Connected to %s: %d tools", name, len(delta.added))` |
| `mcp/manager.py` | `print(f"[mcp] Failed to connect ...")` | `logger.error("Failed to connect to %s: %s", name, exc)` |
| `agent/core.py` | `_auto_save()` 静默失败 | `logger.warning("Session auto-save failed: %s", exc)` |
| `agent/context.py` | 压缩信息 `get_renderer().info(...)` | 同时写 `logger.info("Conversation compacted")` |
| `sandbox/manager.py` | 无启动日志 | `logger.info("Sandbox started: backend=%s profile=%s", ...)` |
| `__main__.py` | CLI 入口无日志 | `logger.info("Session started: model=%s thread=%s", ...)` |

#### 4.1.3 收敛 TUI 和日志的职责

| 输出目标 | 用什么 | 说明 |
|---------|--------|------|
| 面向用户的对话/工具结果 | `get_renderer()` (rich TUI) | 不变 |
| 面向开发者的诊断/错误 | `logging` | 新增 |
| Agent 循环状态（thinking 等） | `get_renderer()` + `_emit_text()` | 不变 |

---

### 4.2 错误处理加固

**现状**：多处 `except Exception: pass` 或 `except Exception` 只记录但继续。

**全局排查**：

```bash
grep -rn "except Exception" src/ | grep -v "__pycache__" | wc -l  # 约 40 处
grep -rn "except:" src/ | wc -l  # 裸 except 数量
```

**修复策略**：

| 模式 | 当前 | 改后 |
|------|------|------|
| 非关键路径失败 | `except Exception: pass` | `except Exception: logger.debug("...", exc_info=True)` |
| 关键路径失败 | `except Exception: pass` | `except Exception: logger.error("...")` + diagnostics |
| 用户可见错误 | `return f"Error: {e}"` | ✅ 不变，保持用户可见错误信息 |
| 资源清理 | `except Exception: pass` | ✅ 不变，资源清理不需要报错 |

**具体修复清单**：

| 文件:行 | 当前代码 | 改后 |
|---------|---------|------|
| `context.py:118` | `except Exception: pass` | ✅ 已修复 → `self._diagnostics.append(...)` |
| `context.py:188` | `except Exception: pass` | ✅ 已修复 |
| `context.py:199` | `except Exception: pass` | ✅ 已修复 |
| `mcp/manager.py:73` | `except Exception as exc: ...print(...)` | 改为 `logger.error(...)` |
| `mcp/manager.py:105` | `except Exception as exc: ...append(...)` | 改为 `logger.warning(...)` |
| `mcp/manager.py:160` | `except Exception as exc: ...append(...)` | 改为 `logger.warning(...)` |
| `sandbox/backend.py:59` | `except Exception as e: return CommandResult(error=...)` | ✅ 不变：返回带错误的结果是正确策略 |
| `sandbox/bwrap_backend.py:55` | `except Exception as e: raise RuntimeError(...)` | ✅ 不变 |
| `hooks/runner.py:55` | `except Exception as exc: return HookOutput(...)` | ✅ 不变：hook 失败不应中断主流程 |
| `tools/builtin.py:45` | `except Exception: pass` (auto_update_memory_index) | `except Exception: logger.debug(...)` |
| `tools/builtin.py:148` | `except Exception: pass` (grep fallback) | `except Exception: logger.debug("grep failed, fallback to Python")` |
| `tools/builtin.py:167` | `except Exception: pass` (walk dir) | `except OSError: pass` (更精确) |
| `__main__.py:230` | `except Exception as e:` | 改为 `logger.error("CLI fatal: %s", e)` + 重新抛出 |

---

## 优先级 1-4 完成后的最终状态

### 完成后的项目画像

```
nanocode/
├── .github/
│   └── workflows/
│       └── ci.yml                    ✅ CI: lint → type-check → test
├── src/
│   ├── logging_config.py             ✅ 全局日志配置
│   ├── __main__.py                   ✅ 使用 logger
│   ├── domains/
│   │   └── ...                       ✅ 所有模块 logger + 类型注解
│   ├── runtime/
│   │   └── agent/
│   │       ├── loop.py               ✅ 依赖 ModelProvider 协议
│   │       ├── backends.py           ✅ 删除 _call_*_stream (迁移到 providers/)
│   │       └── context.py            ✅ 类型注解完善 + 错误处理
│   └── providers/                    ✅ 唯一的 provider 实现
│       ├── anthropic.py              ✅ 完整实现 + thinking + retry
│       ├── openai_chat.py            ✅ 完整实现
│       └── registry.py               ✅ provider 工厂
├── test/                             ✅ 177+ 测试
├── pyproject.toml                    ✅ [tool.ruff] [tool.mypy] [tool.pytest]
└── README.md                         ✅ 项目介绍 + 架构图 + Demo
```

### 面试时能自信回答的问题

| 问题 | 回答要点 |
|------|---------|
| "架构怎么样？" | 领域驱动设计：domains(工具/沙箱/MCP/权限) → runtime(Agent循环) → providers(模型适配) → capabilities(插件)，通过 Protocol 解耦 |
| "如何保证代码质量？" | CI 自动化 lint → type-check → test 三步，ruff + mypy + 177 个测试 |
| "怎么支持新模型？" | 实现 `ModelProvider` 协议一个类就行，loop/context 零改动。当前支持 Anthropic 和 OpenAI，加 Gemini 只需 `GeminiProvider` |
| "开发过程？" | 20+ 个语义 commit，可以看到从工具系统 → Agent循环 → MCP → 安全加固的渐进过程 |
| "遇到什么难点？" | (1) Anthropic/OpenAI 流式解析差异 (2) 上下文压缩如何不破坏 tool_use↔tool_result 配对 (3) hook 修改输入后的重校验 (4) 子Agent 权限继承的安全模型 |

---

## 实施顺序建议

| 阶段 | 内容 | 预计工作量 |
|------|------|-----------|
| 第 1 天 | Git 历史重构 + CI 配置 + pyproject.toml 工具配置 | 2-3 小时 |
| 第 2 天 | ruff fix + ruff format + mypy 修复（第一轮） | 2-3 小时 |
| 第 3 天 | 日志系统替换 print() → logging | 1-2 小时 |
| 第 4 天 | 错误处理: `except Exception: pass` → diagnostics | 1-2 小时 |
| 第 5-6 天 | Provider 迁移: AnthropicProvider 补齐 + AgentLoop 改造 | 3-5 小时 |
| 第 7 天 | 回归测试 + README 更新 + 最终演示准备 | 2 小时 |

总计约 14-20 小时，一周内可完成。
