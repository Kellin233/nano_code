# Nano Code 模块测试与审查报告

日期：2026-06-06

## 结论

本轮补充了 `nanocode/test/v1/` 下的复杂场景和异常场景测试，并重新执行编译与全量单元测试。

总体结果：通过。

执行结果：

```bash
python -m pip install -e .
python -m compileall src test
# PASS

python -m unittest discover -s test/v1 -v
# Ran 21 tests
# OK

python -m unittest discover -s test -v
# Ran 76 tests
# OK
```

本轮发现并修复 1 个边界问题：

- `grep_search` 在 Python fallback 路径遇到非法正则时会抛出异常；已改为返回工具错误：`Error: invalid regex: ...`。这符合“工具错误作为数据返回给模型”的主循环契约。

## 新增测试文件

新增测试均位于 `test/v1/`：

- `test_agent_event_loop_v1.py`
- `test_cli_models_builtins_v1.py`
- `test_memory_skill_session_context_v1.py`
- `test_permissions_hooks_sandbox_v1.py`
- `test_registry_mcp_v1.py`
- `test_tool_runtime_v1.py`

`test/v1/__init__.py` 仅用于 `unittest discover` 从 `test` 顶层递归发现 v1 测试。

## 模块测试结果

| 模块 | 复杂/异常场景 | 结果 | 审查结论 |
|---|---|---:|---|
| CLI：`__main__.py` | `--yolo`、`--dont-ask` 同时出现时的权限优先级；one-shot prompt 分词保留 | PASS | 参数解析和权限模式映射正常。交互式 REPL、信号处理仍主要依赖人工/集成测试。 |
| Agent 事件流：`agent/core.py`、`engine.py`、`loop.py`、`events.py` | Anthropic 工具调用回灌；OpenAI malformed tool arguments；Stop hook 追加上下文并强制再跑一轮 | PASS | 事件流主循环能保持 tool_use/tool_result 配对，工具错误能回灌给模型，Stop hook 可控制继续。 |
| Backend/Model：`agent/backends.py`、`models.py` | OpenAI tool schema 转换；429 retry 后成功；流式回调由 fake backend 驱动 | PASS | 后端职责已收敛为模型协议适配。测试未访问真实 Anthropic/OpenAI API。 |
| Context：`agent/context.py` | 高上下文压力下重复读取同一文件，只保留最后一次工具结果，其余 snip | PASS | 压缩不变量正常：保留工具调用元数据，裁剪旧结果正文。重复 read_file 的策略是只保留最新结果。 |
| Tool 契约：`tools/base.py`、`registry.py`、`runtime.py` | schema validation 失败；PreToolUse 修改输入后仍进入权限；PostToolUse 追加上下文；大结果落盘；MCP 工具包装 | PASS | ToolRuntime 管线顺序正确：validate -> PreToolUse -> permission -> execute -> persist -> PostToolUse。 |
| Builtin tools：`tools/builtin.py` | `list_files` 跳过 `.git`/`__pycache__`；非法 grep regex；读前写保护由既有测试覆盖 | PASS | 本轮修复非法正则崩溃。文件工具和 shell 工具基础行为正常。 |
| Permissions：`permissions/` | protected path 在 `dontAsk` 下拒绝；复杂 shell expansion 需要确认；deny 规则优先于 bypass 的既有测试 | PASS | 安全边界符合重构目标：`bypassPermissions` 不绕过 deny/protected path。 |
| Hooks：`hooks/` | 用户 hooks 加载；项目 hooks 需要 `NANO_CODE_TRUST_PROJECT_HOOKS=1`；fail_closed + 非 JSON 输出拒绝 | PASS | hooks 默认信任边界合理。命令式 hooks 的安全仍依赖用户显式信任项目配置。 |
| Sandbox：`sandbox/` | `auto` 模式 SDK 不存在时降级本地；readonly workspace/no-network 配置；显式 microsandbox 不降级由既有测试覆盖 | PASS | shell 后端抽象正常。真实 microsandbox 容器启动未在本地测试中执行，属于外部依赖集成风险。 |
| Memory：`memory/` | side query 返回非 JSON 时 fallback；大 memory 截断；already surfaced 去重；既有保存/索引/压缩测试 | PASS | 召回降级路径正常，不会因 side query 异常阻塞主循环。 |
| Skill：`skill/` | 项目级 skill 覆盖用户级；`user-invocable: false`；fork context；allowed-tools JSON；参数占位符渲染 | PASS | discovery 只读 metadata、调用时懒加载正文的设计正常。 |
| Subagent：`subagent.py` | 自定义 agent frontmatter；allowed-tools 白名单；未知类型回退由既有逻辑覆盖 | PASS | 只读 plan/explore 约束和自定义 agent 过滤正常。 |
| MCP：`mcp_client.py` + `ToolRegistry` | MCP 工具通过 ToolRuntime 路由；缺失 manager 返回工具错误 | PASS | registry 层 MCP 适配正常。未启动真实 MCP subprocess，真实 JSON-RPC 连接仍需集成测试。 |
| Session：`session.py` | corrupt JSON 忽略；latest session 按 `startTime` 排序 | PASS | 会话文件读写和容错正常。 |
| Prompt/Frontmatter：`prompt.py`、`frontmatter.py` | `CLAUDE.md` include；deferred tools 注入；frontmatter value 含冒号；缺失结束分隔符 | PASS | prompt 动态上下文组装正常。frontmatter 解析符合轻量 parser 预期。 |
| UI：`ui.py` | 通过 Agent 事件渲染路径间接覆盖工具调用/结果输出 | PASS | 未做像素级或终端控制序列断言；当前 UI 主要是 smoke coverage。 |

## 审查结果

未发现阻塞问题。

当前结构和 `remake/design/total.md` 的目标一致：

- 主入口已经走事件流：`Agent.chat()` 消费 `SessionEngine.submit()` 事件。
- 工具调用统一走 `ToolRuntime`，权限、hooks、大结果处理集中。
- 权限策略已把 protected path、deny rule 放在 bypass 之前。
- hooks 默认只加载用户级配置，项目级配置必须显式信任。
- shell 沙箱作为可选后端接入，不影响默认本地行为。

## 剩余风险

- 真实 Anthropic/OpenAI API streaming 未在本轮测试中访问；当前使用 fake stream 验证消息回灌和事件流逻辑。
- 真实 MCP server subprocess 未启动；只验证了 registry/runtime 路由和错误路径。
- 真实 microsandbox 容器未启动；只验证 SDK 不存在时的 fallback/strict 行为和 manager 路径映射。
- REPL 的人工交互、Ctrl+C 信号处理、终端 UI 控制序列未做端到端自动化。
- hooks 是 shell 命令扩展点，功能正常，但项目 hooks 的信任开关必须继续保持默认关闭。

## 建议后续测试

- 增加一组带 fake MCP subprocess 的 JSON-RPC 端到端测试。
- 在有 microsandbox SDK 和运行环境的 CI job 中增加 `--sandbox microsandbox` 集成测试。
- 增加少量 CLI subprocess 级测试，覆盖 one-shot、`--resume`、`--dont-ask`、`--sandbox` 参数组合。
- 对 Anthropic/OpenAI stream adapter 加 provider event fixture 测试，减少真实 SDK 行为变化带来的回归风险。
