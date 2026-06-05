# Skill 改进方案

## 目标

把当前 `mini_claude/skills.py` 从“能发现并展开 `SKILL.md` 的工具函数”升级成一个稳定、可解释、可测试的 Skill Runtime。

本轮改进聚焦四个点：

- `SkillRegistry`：解决“发现什么”
- `SkillInvocation`：解决“如何调用”
- `ActiveSkillManager`：解决“调用后如何持续生效”
- 三层阶段式披露：解决“只在需要时给模型刚好够用的上下文”

本方案优先满足要求和稳定交付，不追求一次性复刻 Claude Code 的全部高级功能。

## 重构记录

### 2026-06-05：补强三层披露说明与 metadata 输出

本次重构聚焦可读性和 system prompt 精简，不改变 skill 调用协议：

- `mini_claude/skills.py` 补充中文模块注释和类注释，明确每个运行时对象的职责。
- 在代码注释中明确“三层阶段式披露”：
  - 第一层：system prompt 只披露 skill metadata。
  - 第二层：用户或模型调用后才披露完整 `SKILL.md` body。
  - 第三层：supporting files 不自动注入，只通过 `${CLAUDE_SKILL_DIR}` 和 `read_file` 按需读取。
- `build_skill_descriptions()` 从 user/model 两段列表改为单列表格式，每个 skill 只出现一次，并用 `invoke:` 标明用户和模型触发方式，减少重复 prompt。
- `SkillRegistry` 已改成真正的 lazy body loading：发现阶段只读取 frontmatter metadata 和文件路径，不读取 `SKILL.md` 正文。
- `SkillInvocation.render_prompt()` 在调用阶段通过 `skill.path` 读取完整 `SKILL.md` body；手动构造的 `SkillDefinition(prompt_template=...)` 仍作为兼容路径保留。

这次重构后，skill 系统同时满足“上下文渐进披露”和“文件级懒加载”。发现阶段只回答“有哪些 skill 可用”，调用阶段才付出正文读取和上下文成本。

## 重构前问题

重构前实现已经有 Claude Code Skills 的雏形：

- 支持 `~/.claude/skills/<name>/SKILL.md`
- 支持 `.claude/skills/<name>/SKILL.md`
- 支持解析基础 frontmatter
- 支持把 skill 描述注入 system prompt
- 支持用户通过 `/<skill>` 调用
- 支持模型通过 `skill` 工具调用
- 支持 `context: inline` 和 `context: fork`

但当时仍有几个核心缺口：

- 发现、解析、调用、上下文生命周期混在几个函数里，边界不清。
- metadata 支持不完整，字段名兼容性也不够，例如当前样例写 `user_invocable`，代码只读 `user-invocable`。
- 用户调用和模型调用路径不统一，fork skill 用户调用时还要让模型再调用一次 `skill` 工具，稳定性不足。
- 调用后的 skill 没有被单独记录，压缩上下文后可能丢失。
- 只有 metadata -> `SKILL.md` 两层披露，supporting files 没有明确约定。

## 核心设计

### 1. SkillRegistry：发现什么

`SkillRegistry` 负责发现、解析、校验和暴露 skill metadata。

它回答的问题是：

```text
当前会话有哪些 skill 可用？
这些 skill 来自哪里？
哪些可以被用户调用？
哪些可以被模型自动调用？
同名 skill 谁覆盖谁？
哪些 metadata 应该注入 system prompt？
```

第一版需要支持的来源：

- 用户级：`~/.claude/skills/<name>/SKILL.md`
- 项目级：`./.claude/skills/<name>/SKILL.md`

覆盖规则：

- 先加载用户级 skill
- 后加载项目级 skill
- 同名时项目级覆盖用户级

第一版需要支持的 metadata：

```text
name
description
when_to_use / when-to-use
user_invocable / user-invocable
disable_model_invocation / disable-model-invocation
allowed_tools / allowed-tools
disallowed_tools / disallowed-tools
context
agent
argument_hint / argument-hint
source
skill_dir
```

第一版必须做的校验：

- `name` 为空时使用目录名。
- `description` 允许为空，但应该保留字段。
- `context` 只允许 `inline` 或 `fork`，非法值回退到 `inline`。
- `allowed_tools` / `disallowed_tools` 同时支持逗号字符串和 JSON 数组。
- `user_invocable` 和 `user-invocable` 都要兼容。
- `disable_model_invocation` 和 `disable-model-invocation` 都要兼容。
- 解析失败的 skill 不应中断整个程序，应跳过并记录错误信息。

第一版不做：

- enterprise / managed skills
- plugin skills
- MCP skills
- `.claude/commands/*.md` 兼容
- live change detection
- skillOverrides
- 跨目录复杂优先级

原因：这些能力偏生态和产品集成，先做会扩大实现面，影响稳定交付。

### 2. SkillInvocation：如何调用

`SkillInvocation` 负责把一次 skill 调用转成可执行动作。

它回答的问题是：

```text
这次调用来自用户还是模型？
调用者是否允许触发这个 skill？
参数如何解析和替换？
完整 SKILL.md 如何渲染？
inline 还是 fork？
fork 时使用哪个 agent？
工具权限如何应用？
```

调用来源分两类：

```text
user: 用户输入 /<skill-name> ...
model: 模型调用 skill 工具
```

调用控制：

- `user_invocable: false`：不允许用户通过 `/<skill>` 调用。
- `disable_model_invocation: true`：不允许模型通过 `skill` 工具自动调用。
- 用户调用和模型调用必须走同一个 invocation 逻辑，避免两条路径行为不一致。

参数替换第一版需要支持：

```text
$ARGUMENTS
${ARGUMENTS}
$0, $1, $2 ...
$ARGUMENTS[0], $ARGUMENTS[1] ...
${CLAUDE_SKILL_DIR}
```

第一版暂不做命名参数 `$name` 和 `arguments` schema。原因是命名参数需要更清楚的参数解析规则，否则容易引入不稳定行为。

如果用户传入了参数，但 skill 正文没有使用任何参数占位符，第一版应在渲染后的 prompt 末尾追加：

```text
ARGUMENTS:
<args>
```

这样可以避免参数被静默丢弃。

inline 调用：

- 渲染完整 `SKILL.md` body。
- 作为一条用户消息进入主 Agent。
- 记录到 `ActiveSkillManager`。

fork 调用：

- 渲染完整 `SKILL.md` body。
- 创建隔离子 Agent 执行。
- 子 Agent 不继承主对话历史。
- 子 Agent 的结果返回主 Agent。
- 记录到 `ActiveSkillManager`，但记录类型应标记为 `fork`。

`agent` 字段：

- 如果 `context: fork` 且声明了 `agent`，优先使用该 agent 类型。
- 如果没有声明 `agent`，默认使用 `general`。
- 如果声明的 agent 不存在，回退到 `general`，并在结果中说明回退。

工具策略第一版采用稳定语义：

- `allowed_tools`：只作为 fork skill 的工具白名单使用，保持当前实现语义，避免引入复杂权限预批准。
- `disallowed_tools`：从可用工具列表中移除指定工具。

注意：这和 Claude Code 的完整语义不完全一致。Claude Code 里的 `allowed-tools` 更接近预批准工具，而不是唯一工具池。但当前项目还没有完整权限系统和 hook/sandbox 管线，第一版先保留白名单语义更稳定。

第一版不做：

- 动态 shell 注入 `!command`
- fenced shell injection
- `allowed-tools` 的预批准权限语义
- `Skill(name)` / `Skill(name *)` 权限规则
- skill 调用的独立权限弹窗

原因：这些能力需要先完成 hook、permission、sandbox 的基础设施，否则安全边界不清。

### 3. ActiveSkillManager：调用后如何持续生效

`ActiveSkillManager` 负责维护当前会话中已经激活过的 skill。

它回答的问题是：

```text
当前会话调用过哪些 skill？
最近一次调用是什么时候？
渲染后的 skill 内容是什么？
上下文压缩后应该重新挂载哪些 skill？
每个 skill 最多占多少上下文？
所有 active skills 总共最多占多少上下文？
```

第一版需要记录：

```text
skill name
source
skill_dir
context: inline/fork
rendered_prompt
args
invoked_by: user/model
last_used_at
approx_token_count
```

记录策略：

- 同一个 skill 多次调用时，更新最近一次调用记录。
- 保留最近调用的参数和渲染后 prompt。
- 默认最多记录 8 个 active skills。

重挂策略：

- 在手动 `/compact` 和自动 compact 后，重新注入最近 active skills。
- 按最近使用时间倒序重挂。
- 单个 skill 最多保留约 5000 tokens。
- 所有 active skills 总预算约 25000 tokens。
- 超出预算时跳过更早的 skill。

第一版可以用粗略字符数估算 token：

```text
approx_tokens = len(text) // 4
```

不需要第一版引入 tokenizer。稳定交付优先。

重挂内容格式：

```text
[Active skill: <name>]
Invoked by: user/model
Context: inline/fork
Arguments: ...

<rendered skill prompt, possibly truncated>
```

第一版不做：

- 持久化 active skills 到磁盘
- 跨 session 恢复 active skills
- 精确 tokenizer 统计
- 对 supporting files 的单独生命周期管理

原因：当前 session 保存仍是简单 JSON，先把运行期行为做稳定，再考虑持久化。

### 4. 三层阶段式披露

Skill 的上下文披露分三层：

```text
第一层：metadata
第二层：SKILL.md
第三层：supporting files
```

#### 第一层：metadata 披露

启动和构建 system prompt 时，只注入轻量信息：

```text
name
description
when_to_use
是否用户可调用
是否模型可调用
```

好处：

- 节省上下文。
- 降低无关 skill 对模型的干扰。
- 让模型在选择阶段只关注“该不该用这个 skill”。

#### 第二层：SKILL.md 披露

当用户或模型触发 skill 后，才加载完整 `SKILL.md` body。

好处：

- 只有真正需要时才付出上下文成本。
- skill 可以写完整工作流，而不污染所有会话。
- 调用后的内容可以交给 `ActiveSkillManager` 维护。

#### 第三层：supporting files 披露

skill 目录可以包含参考文件、示例和脚本：

```text
my-skill/
├── SKILL.md
├── references.md
├── examples.md
└── scripts/
    └── helper.py
```

第一版要求：

- `SKILL.md` 中明确可以使用 `${CLAUDE_SKILL_DIR}` 引用同目录资源。
- 不主动扫描并注入所有 supporting files。
- 模型需要时通过 `read_file` 读取具体文件。
- `SKILL.md` 应该写清楚哪些 supporting files 存在以及何时读取。

第一版不做：

- 自动生成 supporting files 清单
- 自动读取 supporting files
- supporting files 的预算管理
- supporting files 的压缩后重挂

原因：第三层的关键是“按需读取”，不是系统替模型一次性加载所有资源。先建立目录约定和提示规则即可。

## 建议模块结构

第一版可以继续保留 `mini_claude/skills.py` 文件，但内部按职责拆分。不要为了炫技拆太多文件。

建议结构：

```text
skills.py
  SkillDefinition
  SkillRegistry
  SkillInvocation
  ActiveSkill
  ActiveSkillManager
  compatibility functions
```

为了减少改动面，保留现有函数名作为兼容层：

```text
discover_skills()
get_skill_by_name()
resolve_skill_prompt()
execute_skill()
build_skill_descriptions()
reset_skill_cache()
```

这些函数内部转调新的 registry / invocation / active manager。

## 与现有代码的集成点

### `mini_claude/skills.py`

需要做：

- 引入 `SkillRegistry`
- 引入 `SkillInvocation`
- 引入 `ActiveSkillManager`
- 增强 frontmatter 解析
- 增强参数替换
- 保留兼容函数

不能做：

- 不要引入复杂外部依赖。
- 不要把 sandbox、hook、MCP 逻辑放进 skills.py。
- 不要在发现阶段读取 supporting files。

### `mini_claude/agent.py`

需要做：

- `_execute_skill_tool()` 改为调用统一的 `SkillInvocation`。
- inline skill 调用后通知 `ActiveSkillManager`。
- fork skill 调用后通知 `ActiveSkillManager`。
- compact 后重新注入 active skills。

不能做：

- 不要重写整个 agent loop。
- 不要改变普通工具调用协议。
- 不要把 skill 状态和 memory 状态混在一起。

### `mini_claude/__main__.py`

需要做：

- `/<skill>` 用户调用也走统一 `SkillInvocation`。
- fork skill 用户调用时不要再让模型“自己调用 skill tool”，应直接执行 invocation。
- `/skills` 展示 user-invocable skills，同时可以标注 source 和 context。

不能做：

- 不要新增复杂交互 UI。
- 不要做技能安装器或 marketplace。

### `mini_claude/prompt.py`

需要做：

- `build_skill_descriptions()` 保持 metadata-only。
- 明确告诉模型：完整 skill 内容只会在调用后加载。
- 明确告诉模型：supporting files 需要通过 `${CLAUDE_SKILL_DIR}` 和 `read_file` 按需读取。

不能做：

- 不要把所有 `SKILL.md` 正文塞进 system prompt。
- 不要把 supporting files 自动注入 system prompt。

## 交付顺序

按以下顺序实现，风险最低：

```text
1. 重构 SkillDefinition 和 SkillRegistry
2. 保持现有 discover/build_skill_descriptions 行为不破
3. 增强 metadata 字段兼容和解析
4. 实现 SkillInvocation，统一用户调用和模型调用路径
5. 补齐参数替换
6. 实现 ActiveSkillManager 的记录能力
7. 接入 compact 后 active skill 重挂
8. 更新 README / 测试文档
```

不要先做 dynamic shell injection、plugin skills 或完整权限语义。这些属于后续阶段。

## 验证方式

基础验证：

```bash
python -m compileall mini_claude
mini-claude --help
mini-claude --yolo "/skills"
```

手动场景：

```text
1. 创建 .claude/skills/greet/SKILL.md
2. 输入 /skills，确认能看到 greet
3. 输入 /greet Alice，确认参数被传入
4. 创建 context: fork 的 skill，确认用户手动调用时直接 fork 执行
5. 创建 user_invocable: false 的 skill，确认 /skills 不展示且 /name 不允许
6. 创建 disable_model_invocation: true 的 skill，确认模型调用 skill 工具时被拒绝
7. 调用 skill 后执行 /compact，确认 active skill 被重新注入
```

单元测试建议：

- frontmatter 字段兼容：`user_invocable` / `user-invocable`
- tool list 解析：逗号字符串和 JSON 数组
- 参数替换：`$ARGUMENTS`、`$0`、`$ARGUMENTS[0]`
- 同名覆盖：project 覆盖 user
- invocation 控制：用户调用和模型调用权限
- active skill budget：超预算截断或跳过

## 明确不做的内容

本轮不做：

- dynamic shell injection：`!command`
- fenced shell injection
- plugin skills
- MCP skills
- enterprise / managed skills
- `.claude/commands` 兼容
- skill marketplace
- live change detection
- `skillOverrides`
- 完整 `allowed-tools` 预批准语义
- `Skill(name)` 权限规则
- supporting files 自动注入
- active skills 跨 session 持久化
- 精确 tokenizer

这些内容不是没有价值，而是依赖 sandbox、hook、permission、plugin provider 或 session store 的进一步重构。当前阶段强行加入会扩大风险，不利于稳定交付。

## 成功标准

本轮完成后，skill 系统应满足：

- 能清楚回答“有哪些 skill 可用”。
- 用户调用和模型调用走统一路径。
- 参数不会被静默丢弃。
- `context: fork` 的用户调用不再依赖模型二次触发。
- 调用过的 skill 会被记录，并在 compact 后按预算重挂。
- system prompt 仍然只注入 metadata，不注入所有 skill 正文。
- supporting files 有明确目录约定，但不会被自动塞进上下文。

达到这些标准后，`miniclaude` 的 skill 就不只是 prompt 片段，而是一个可管理、可压缩、可扩展的 Skill Runtime。
