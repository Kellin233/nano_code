# 智能体循环

## 概述

Agent 循环是 nanocode 的核心——它驱动"用户输入 → 模型调用 → 工具执行"的完整对话流程。架构从 Mixin 模式重构为三个独立组件：`Agent`（状态容器）、`AgentLoop`（主循环）、`Backend`（模型调用）。

## 架构图

```
用户输入
    │
    ▼
AgentLoop.run(prompt)
    │
    ├── 注入启动上下文（仅首次）
    ├── 准备动态附件（Skill/Deferred工具列表）
    ├── 添加用户消息
    ├── 初始化 MCP（仅首次）
    ├── 压缩检查
    ├── 记忆召回
    │
    └── 主循环 ────────────────────────┐
        │                              │
        ├── 压缩流水线（每轮）          │
        ├── 消费记忆预取结果            │
        ├── Backend.call() 调用模型     │
        ├── 记录 token 用量             │
        │                              │
        ├── 有 tool_calls？             │
        │   ├── 预算检查                │
        │   ├── ToolRuntime 执行工具    │
        │   ├── 追加结果到消息历史      │
        │   └── 继续循环 ──────────────►┘
        │
        └── 无 tool_calls？
            ├── Stop hook 检查
            └── LoopFinished("stop")
```

## 核心组件

### Agent——纯状态容器

```python
class Agent:
    """持有一次对话的所有状态，不实现行为。"""
    # 配置与标识
    config: RuntimeConfig
    session_id: str

    # 消息历史（Anthropic/OpenAI 分开存储）
    _anthropic_messages: list[dict]
    _openai_messages: list[dict]

    # Token 与预算
    total_input_tokens: int
    total_output_tokens: int
    current_turns: int

    # 能力模块
    _tool_registry: ToolRegistry
    _sandbox_manager: SandboxManager
    _mcp_manager: McpManager
    _hook_manager: HookManager
    _skill_invocation: SkillInvocation
    _active_skills: ActiveSkillManager
```

**为什么从 Mixin 改为纯状态容器**：原来的 Agent 通过 3 个 Mixin（ContextMixin、ToolRuntimeMixin、BackendMixin）拼装行为。Mixin 通过 `self._anthropic_messages` 等方式隐式访问状态——改了字段名 Mixin 就崩了，阅读时需要在 4 个文件间跳转。现在行为全部外移，Agent 只暴露 getter/setter，依赖关系显式可见。

### AgentLoop——后端无关的主循环

```python
class AgentLoop:
    def __init__(self, agent, backend: Backend):
        ...
    
    async def run(self, user_message: str) -> AsyncIterator[RuntimeEvent]:
        """驱动一次完整对话，产出事件流"""
```

**关键设计**：`AgentLoop` 只依赖 `Backend` 接口（`backend/base.py`），不依赖具体的 `AnthropicBackend` 或 `OpenAIBackend`。这是依赖倒置——上层（循环）依赖抽象（接口），下层（后端实现）也依赖抽象。

**为什么消除双后端重复代码**：原来 `agent/loop.py` 有 `_run_anthropic`（~100行）和 `_run_openai`（~100行）两个方法，80% 的代码相同。现在后端差异被封装在 `Backend.call()` 中，循环本身只有一套。

### Backend——策略模式

```python
class Backend(ABC):
    async def call(self, *, messages, system, tools,
                   on_text_delta, thinking_mode) -> BackendResponse: ...
    def supports_thinking(self, model) -> bool: ...
```

`AnthropicBackend` 处理 Anthropic Messages API 的流式解析（`content_block_start/delta/stop` 事件），`OpenAIBackend` 处理 OpenAI Chat Completions 的流式解析（增量 `tool_calls` 拼接）。两者都返回统一的 `BackendResponse`。

**为什么双消息历史刻意不统一**：`_anthropic_messages` 和 `_openai_messages` 分开存储。Anthropic 的 `tool_use/tool_result` block 和 OpenAI 的 `function call/role:tool` 格式差异太大，强行抽象反而增加复杂度。

### 事件驱动模型

所有循环产出通过 `RuntimeEvent` 流传递。使用工厂函数替代子类：

```python
ToolCallStarted(call)    # → RuntimeEvent(type="tool.started")
LoopFinished("stop")     # → RuntimeEvent(type="turn.finished")
```

不同的消费端（一次性模式、TUI、Server）各自消费事件流，互不依赖。

### 子 Agent fork

子 Agent 复用同一个 `Agent` 类，通过 `RuntimeConfig(is_sub_agent=True, custom_system_prompt=...)` 和 `custom_tools` 创建。独立消息历史，共享父 Agent 的 sandbox。递归防护：所有子 Agent 不给 `agent` 工具。

## 面试考点

**Q: 如果要加第三个模型厂商（如 Google Gemini），需要改哪些文件？**

只需要两个地方：`models.py` 加模型元数据，新建 `backend/gemini.py` 实现 `Backend` 接口。循环和 Agent 状态完全不用动。
