# Nano Code TUI 重构设计

## 背景

当前交互入口主要由 `nanocode/__main__.py` 中的 REPL 循环和 `nanocode/ui.py` 中的一组 `print_*()` 函数组成。这个实现足够简单，但随着工具、MCP、skills、memory、sandbox、hooks 等能力增加，终端交互已经出现几个结构性问题：

- REPL 输入仍是 `input()`，缺少历史、补全、多行和外部编辑器输入。
- 本地命令散落在 `run_repl()` 的 if/else 中，扩展性差。
- 输出渲染由全局函数承担，Agent 核心直接 import UI 函数，边界不够干净。
- 工具调用、工具结果、状态、错误、成本和确认提示缺少统一视觉语义。
- spinner 使用独立线程直接写 stdout，容易和流式输出互相覆盖。

本设计参考 OpenAI Codex CLI 的终端交互风格，但不复制 Codex CLI 的全部功能。Codex CLI 的重点不是复杂全屏界面，而是 terminal-native 的 agent chat：历史输出保持可复制，正在编辑的 composer 独立显示，slash commands 有即时提示，状态栏持续说明当前模型、目录和工作状态。

## 目标

- 布局清晰合理：保持终端对话流，不做全屏 dashboard。
- 人机交互体验良好：支持历史、补全、多行、编辑器输入、清晰中断和可读输出。
- 逻辑合理：把输入、命令、渲染和 Agent 运行解耦，降低 `__main__.py` 和 `ui.py` 的职责压力。
- 代码风格简洁务实：按需抽象，不炫技，不偷工减料，不为了未来能力过度拆分。
- 易维护和可扩展：后续添加命令、主题、日志、工具输出样式时，不需要改 Agent 主循环。

## 非目标

- 不实现全屏 TUI、IDE 式布局、面板仪表盘或 curses/Textual 应用。
- 不实现 Codex CLI 的 sandbox 管理、approval 工作流、review 面板、IDE 集成、voice、图片粘贴等非 TUI 范围能力。
- 不改变 Agent 核心行为、工具执行语义、权限语义、会话保存语义或模型调用语义。
- 不把 Rich markup、终端颜色或 prompt_toolkit 细节写入模型上下文或 session history。

## 总体设计

TUI 重构分成 5 个子设计：

1. `TuiApp`：REPL 主控制器。
2. `TuiInput`：Codex-style composer 输入系统。
3. `TuiRenderer`：输出渲染。
4. `TuiCommands`：本地命令注册表。
5. `UiEventAdapter`：Agent 事件到 UI 表现的适配层。

关系如下：

```text
__main__.py
  -> 解析 CLI / API / sandbox / permission
  -> 创建 Agent
  -> TuiApp.run()

TuiApp
  -> TuiInput.read()
  -> TuiCommands.dispatch()
  -> Agent.chat()
  -> TuiRenderer.render(...)

Agent / SessionEngine / AgentLoop
  -> 产出事件或调用兼容 UI adapter
```

设计原则：

- `__main__.py` 只保留启动配置，不再承担交互命令和渲染细节。
- `Agent` 不直接知道 prompt_toolkit，也不直接持有 composer 布局状态。
- `TuiRenderer` 是唯一掌管终端表现的对象。
- `TuiCommands` 是可测试的命令注册表，不再使用长 if/else 分发。
- 输入 composer、历史 transcript、状态栏和补全菜单分层实现，避免一个函数同时处理布局、命令和模型调用。

## 模块划分

建议新增：

```text
nanocode/tui/
  __init__.py
  app.py          # TuiApp: REPL lifecycle
  input.py        # prompt_toolkit composer/simple input fallback
  renderer.py     # Rich rendering and output policy
  commands.py     # command registry and built-ins
  state.py        # TuiState, command context, settings
  theme.py        # colors/styles, NO_COLOR handling
```

暂不新增 `layout.py`、`widgets.py`、`panels.py`、`views.py`。当前目标不是复杂界面组件，而是把交互边界整理清楚。

## 详细设计

### 1. TuiApp

职责：

- 打印欢迎信息。
- 管理 REPL 生命周期。
- 读取用户输入。
- 判断空输入、退出命令、本地命令、skill 调用和普通聊天。
- 调用 `agent.chat()`。
- 处理中断和最终 cleanup。

建议接口：

```python
class TuiApp:
    def __init__(self, agent: Agent, input: TuiInput, renderer: TuiRenderer, commands: CommandRegistry):
        ...

    async def run(self) -> None:
        ...
```

`TuiApp` 不应解析 CLI 参数，也不应创建 Agent。它只负责交互运行。

### 2. TuiInput

第一优先级是可用性，其次才是高级能力。

能力分层：

- 默认优先使用 `prompt_toolkit`。
- 终端不支持、非 TTY、CI 或初始化失败时，自动 fallback 到简单输入。
- 使用 Codex-style composer：灰色全宽输入块，无标题边框，左侧短 prompt。
- 根据草稿内容、终端宽度、显式换行和宽字符动态计算输入块高度；默认一行，只有内容实际换行时才增高。
- 模型工作期间启用 sticky footer：底部只读 composer/status 区保持可见，流式输出、工具输出和 token 输出发生时先擦除 footer，再输出 transcript，随后重绘 footer。
- 支持输入历史。
- 支持 slash command 补全。
- 支持 skill 名称补全。
- 支持多行输入。
- 支持外部编辑器输入。

建议行为：

- `Enter` 默认提交。
- 多行模式下 `Enter` 插入换行，`Alt+Enter` 或 `Esc Enter` 提交。
- 支持 `{` 开始、`}` 结束的块输入作为 portable fallback。
- `/editor` 打开 `$EDITOR` 编辑当前 prompt。
- 初期不做全仓库文件路径补全，避免慢和复杂。
- 输入提交后由 renderer 追加一条灰色用户消息块；正在编辑的 composer 本身由 prompt_toolkit 擦除，避免重复回显。

建议接口：

```python
class TuiInput:
    async def read(self, prompt: str) -> str | None:
        ...

    def set_completions(self, commands: list[str], skills: list[str]) -> None:
        ...
```

prompt_toolkit 本身是同步交互库，集成时要避免破坏 Agent 的 asyncio 执行。输入阶段可以同步等待；Agent 执行阶段仍由 asyncio 管理。

高度计算沿用 Codex CLI 的核心思想：composer 提供 `desired_height(width)`，外层布局只消费这个结果。Nano Code 不引入完整 widget 系统，而是在 `tui/input.py` 中保留一个小型 `ComposerLayout`，只负责文本显示宽度、换行行数和最大高度上限。

### 3. TuiRenderer

输出风格采用 transcript，而不是全屏刷新。

渲染分类：

- welcome：简洁显示项目名、当前模型、常用命令提示。
- prompt/composer：正在编辑时显示为灰色全宽输入块。
- user message：提交后显示为灰色 transcript 块，和模型输出边界清楚。
- assistant text：保留流式连续输出，不包进卡片。
- sticky footer：真实终端工作状态下显示 `Working`、耗时、只读 composer、模型和目录；非 TTY、CI 和普通日志输出不启用控制序列。
- tool call：一行摘要，展示工具名和关键参数。
- tool result：按类型摘要化。
- file change：diff 友好高亮。
- shell result：命令、退出码、stdout/stderr 摘要。
- status/info：压缩、预算、重试、session restore 等。
- error/warning：醒目但不要刷屏。
- confirmation：独立块展示风险和确认目标。
- cost/tokens：默认在回合结束处简洁显示，并和 assistant text 至少隔出一个空行。

建议接口：

```python
class TuiRenderer:
    def welcome(self, state: TuiState) -> None: ...
    def prompt_marker(self) -> str: ...
    def assistant_delta(self, text: str) -> None: ...
    def tool_call(self, name: str, input: dict) -> None: ...
    def tool_result(self, name: str, result: str) -> None: ...
    def info(self, message: str) -> None: ...
    def error(self, message: str) -> None: ...
    def confirm(self, message: str) -> None: ...
    def cost(self, input_tokens: int, output_tokens: int) -> None: ...
```

关键要求：

- 不把 Rich markup 放入 Agent 消息历史。
- 不直接从多个线程写 stdout。
- `NO_COLOR` 环境变量应禁用彩色样式。
- dumb terminal 下仍可读。
- 工具输出要默认摘要，完整内容通过已有落盘机制或文件路径查看。
- status 行要在模型运行前后显示 `working` / `ready`，让用户知道系统当前是否在等待模型或工具。

### 4. TuiCommands

当前 REPL 命令应从 `run_repl()` 的 if/else 中迁移到命令注册表。

命令接口：

```python
class TuiCommand:
    name: str
    aliases: tuple[str, ...]
    description: str
    usage: str

    async def run(self, ctx: CommandContext, args: str) -> CommandResult:
        ...
```

第一阶段迁移现有命令：

- `/clear`
- `/cost`
- `/compact`
- `/memory`
- `/skills`
- `/exit`
- `/quit`

第二阶段新增：

- `/help`：列出命令。
- `/tokens`：显示 token 和上下文统计。
- `/model`：显示当前模型，后续可支持切换。
- `/editor`：外部编辑器输入。
- `/multiline`：切换多行模式。

原则：

- 不复制 aider 的全部命令。
- 每个命令应可单测。
- 命令输出走 renderer，不直接 print。
- command registry 负责解析和别名匹配。

### 5. UiEventAdapter

当前 Agent 直接 import `print_assistant_text`、`print_tool_call`、`print_tool_result` 等函数。短期可以保留这些函数作为兼容层，但应逐步引入 adapter：

```python
class UiEventAdapter:
    def render_event(self, event: AgentEvent) -> None:
        ...
```

迁移路径：

1. `ui.py` 中的全局函数改为调用默认 renderer。
2. `TuiApp` 创建 renderer 并临时设置为当前 renderer。
3. 后续让 `Agent` 接收 renderer 或 event consumer。
4. 最终 Agent 核心只产出事件，不直接 import terminal UI。

这样可以降低一次性重构风险。

## 硬性约束

- one-shot 模式必须继续可用。
- 非 TTY、CI、管道输入必须可退化运行。
- 不改变 Agent 核心行为、权限语义、工具执行语义和会话保存语义。
- 不让 UI 层直接理解模型 provider 协议。
- 不让 Agent 核心依赖 prompt_toolkit。
- 文本输出必须在普通终端、无颜色环境和日志重定向中可读。
- 兼容 Python `>=3.10`。
- 新增依赖要克制；`prompt_toolkit` 可以作为合理依赖，但不要引入 Textual、curses 之类全屏框架。
- 任何用户确认、危险操作提示、中断语义都不能因为 UI 重构而弱化。

## 隐含要求

- 用户需要的是可控、清楚、不中断思路，而不是复杂界面。
- Codex-style 的重点是 transcript 可读性、composer 输入效率和实时状态，不是复杂布局。
- 工具调用很多时，UI 要帮助用户快速判断系统在做什么、有没有失败、下一步是什么。
- 输出要稳定可复制，便于贴到 issue、日志或文档。
- 后续可能接入更多命令、更多工具和更多模型，所以命令和渲染要可扩展。
- TUI 是 Agent 的表现层，不是业务层。

## 不能做什么

- 不做全屏 IDE 式 TUI。
- 不把 Agent 业务逻辑搬进 TUI。
- 不在 `__main__.py` 继续堆 REPL if/else。
- 不让 Rich markup 泄漏到 Agent 结果或 session history。
- 不把所有工具结果完整刷屏。
- 不复制 aider 的全部功能列表。
- 不为了“像 Codex”引入 sandbox approval、review、IDE、语音、图片粘贴等非 TUI 范围能力。
- 不改变现有 CLI 参数含义。
- 不为了 UI 重构一次性大改 Agent 主循环。

## 可能踩坑

- prompt_toolkit 与 asyncio 混用：输入阶段和 Agent 执行阶段要边界清楚。
- prompt_toolkit 动态高度必须绑定当前 buffer 内容，不能只设置固定 preferred height。
- sticky footer 只能在真实终端启用；日志、管道、CI 或测试输出不能混入光标移动控制序列。
- 宽字符和中文输入会影响终端显示宽度，高度计算不能只按 Python 字符数估算。
- 流式输出和 spinner 冲突：必须统一 stdout 写入入口。
- Ctrl-C 状态不一致：需要区分输入中、Agent 运行中和空闲状态。
- Windows、dumb terminal、NO_COLOR：不要默认假设 ANSI、emoji、宽字符都正常。
- 多行输入提交规则容易反直觉：默认行为要简单，复杂模式用显式开关。
- 命令补全如果扫描全仓库会慢：第一阶段只补全命令和 skill。
- UI 测试不要依赖真实终端：renderer 输出可以用 fake console 或 string buffer 测。
- 过度抽象：不要为了未来主题系统或插件系统提前设计过多层。
- 工具结果摘要过度会影响用户理解：需要保留错误、路径、退出码、摘要和查看完整结果的方法。

## 实施阶段

### 第一阶段：抽离边界

- 新建 `nanocode/tui/`。
- 引入 `TuiApp`、`TuiRenderer`、`TuiCommands` 的最小实现。
- 把欢迎、prompt、命令分发、确认提示从 `__main__.py` 迁出。
- 保持当前 `ui.py` 兼容。
- 添加命令分发和 renderer 单元测试。

验收：

- `nanocode` 交互模式行为基本不变。
- `__main__.py` 明显变薄。
- 文档命令和现有命令可用。

### 第二阶段：输入体验

- 引入 `prompt_toolkit`。
- 实现 Codex-style 灰色 composer，并按内容动态增高。
- 加输入历史。
- 加命令补全。
- 加 skill 补全。
- 加 simple input fallback。
- 实现 `/help`、`/editor`、多行输入基础能力。

验收：

- 普通终端有历史和补全。
- 长输入、中文输入和多行输入会扩展 composer 高度并在上限处滚动。
- 非 TTY 或 dumb terminal 仍能运行。
- 多行输入和外部编辑器输入可用。

### 第三阶段：输出体验

- 重做工具调用摘要。
- 重做工具结果摘要。
- 统一 error、warning、info、confirmation、cost 视觉规则。
- 修正 spinner 与 streaming 输出冲突。
- 支持 NO_COLOR。

验收：

- 工具多轮调用时输出仍清晰。
- 大结果不刷屏。
- 文件修改和 shell 失败一眼可读。

### 第四阶段：Agent/UI 解耦

- 减少 Agent 对全局 `print_*()` 的直接依赖。
- 引入 event adapter 或 renderer 注入。
- 保留兼容层，避免一次性大改。

验收：

- Agent 事件可以被 TUI renderer 或测试 renderer 消费。
- UI 改动不需要修改 Agent 主循环。

### 第五阶段：质量保障

- 添加 TUI command tests。
- 添加 renderer snapshot/string tests。
- 添加 simple input fallback tests。
- 手动验证普通终端、NO_COLOR、非 TTY、Ctrl-C、多行输入。

验收：

- 旧测试通过。
- 新增 TUI 核心路径测试稳定。
- 交互体验符合 terminal-native pair programming 风格。

## 测试策略

- 文档和第一阶段边界迁移：运行 `python -m compileall src test`。
- 命令系统：对 registry、别名、参数解析、错误路径做单元测试。
- renderer：用 string buffer 或 fake console 验证关键输出，不依赖真实终端。
- input fallback：模拟 prompt_toolkit 初始化失败、非 TTY、EOF。
- 跨模块重构后运行：

```bash
python -m unittest discover -s test -v
python -m unittest discover -s test/v1 -v
```

## 最终验收标准

- `nanocode` 交互模式看起来接近 Codex CLI 的 terminal-native chat，而不是零散 print。
- 正在编辑的输入区能按内容动态增高，提交后的用户消息和模型输出边界清楚。
- 常用命令有 `/help` 和补全。
- 多行输入可用。
- 工具输出默认可读，不刷屏。
- Ctrl-C 行为稳定。
- one-shot 模式不受影响。
- 旧测试通过，新增 TUI 单元测试覆盖命令和渲染核心路径。
