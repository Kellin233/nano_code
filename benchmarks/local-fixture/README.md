# Local Fixture Benchmark

`benchmarks/local-fixture` 是 NanoCode 的本地实现回归评测集。它按 NanoCode 当前已经实现的能力设计任务，不移植 Pico 的完整恢复/安全矩阵，也不评测 NanoCode 尚未提供合同的功能。

每个任务都会复制一个干净 fixture workspace，运行普通 `nanocode` 请求，再用本地 shell verifier 和 NanoCode 生成的 run artifacts 评分。runner 不要求额外 trace/report 参数，而是从每个 workspace 的 `.nanocode/runs/<run_id>/` 自动收集 `trace.jsonl`、`report.json` 和 diff。

## 评测目标

这个 benchmark 分三层检查 NanoCode：

- Task Result：目标文件是否被正确修改，verifier 是否通过。
- Agent Process：工具白名单、工具步数、错误恢复、权限/校验事件是否符合预期。
- Run Artifact：`trace.jsonl`、`report.json`、`patch.diff` 是否完整、可审计、schema 稳定。

设计原则：

- 针对 NanoCode 代码实现，而不是照搬 Pico/Claude Code 的能力表。
- 只测已实现合同；未实现的 workspace drift、schema mismatch、durable memory promotion 等不进入任务和指标。
- 任务保持小而确定，过程和结果可以通过 artifacts 复盘。
- 重点模块重点覆盖：工具执行、权限校验、上下文压力、checkpoint/resume、memory 注入、run audit。

## 目录结构

```text
benchmarks/local-fixture/
├── README.md
├── run.py
├── ablation.py
├── artifacts.py
├── contracts.py
├── metrics.py
├── report.py
├── tasks.json
└── fixtures/
    ├── bench_repo_artifacts/
    ├── bench_repo_huge_file/
    ├── bench_repo_large_file/
    ├── bench_repo_memory/
    ├── bench_repo_multifile/
    ├── bench_repo_patch/
    ├── bench_repo_python_bug/
    ├── bench_repo_readme/
    ├── bench_repo_recovery/
    ├── bench_repo_resume/
    ├── bench_repo_resume_matrix/
    ├── bench_repo_security/
    ├── bench_repo_structured/
    ├── bench_repo_tests/
    └── bench_repo_tool_boundary/
```

`tasks.json` 定义任务列表，`fixtures/` 保存初始 workspace。runner 每次执行都会复制 fixture，不会修改基准 fixture。

## 快速运行

在仓库根目录执行：

```bash
cd /path/to/nanocode

python benchmarks/local-fixture/run.py \
  --dry-run \
  --limit 2 \
  --output-root /tmp/nanocode-local-fixture-results \
  --run-name smoke-dry-run
```

`--dry-run` 只验证任务选择并写出空 benchmark artifact，不调用模型。真实执行需要配置模型 API：

```bash
export ANTHROPIC_API_KEY=sk-ant-xxx

python benchmarks/local-fixture/run.py \
  --timeout 180 \
  --output-root benchmarks/local-fixture/results \
  --run-name "core-$(date +%Y%m%d-%H%M%S)" \
  --stream
```

运行全部任务，包括 security 专项：

```bash
python benchmarks/local-fixture/run.py \
  --suite all \
  --timeout 180 \
  --output-root benchmarks/local-fixture/results \
  --run-name "all-$(date +%Y%m%d-%H%M%S)" \
  --stream
```

OpenAI-compatible endpoint 示例：

```bash
export OPENAI_API_KEY=sk-xxx
export OPENAI_BASE_URL=https://your-endpoint/v1

python benchmarks/local-fixture/run.py \
  --model gpt-4o \
  --limit 2
```

## 消融实验

主 benchmark 只回答“当前实现合同是否稳定”。如果要回答“某个模块带来了什么收益”，使用 `ablation.py` 单独跑消融实验：

```bash
python benchmarks/local-fixture/ablation.py \
  --output-root benchmarks/local-fixture/results \
  --run-name "ablation-$(date +%Y%m%d-%H%M%S)" \
  --suite all \
  --timeout 180 \
  --stream
```

默认 ablation 会执行：

- Harness Regression：复用或执行 local-fixture 主回归，统计通过率、预算内完成率、verifier 通过率和 run audit。
- Context Ablation：构造 40 个确定性上下文场景，按 4:3:2:1 覆盖 no-compression baseline、Tool Result Budget、Tool History Snip 和 Context Compact；其中 Tool Result Budget 内部按 5:3:2:2 覆盖刚超过阈值的小型文件读取、明显超过阈值的中型文件读取、大型 grep/search 输出和通过 run_shell 产生的大测试日志 / CI log；分别报告 baseline、压力场景和总体压缩率，避免把压力场景压缩率误读成日常平均值。
- Working Memory Ablation：默认只从已有真实 benchmark rows 聚合；没有真实 rows 时标记 `not_measured`，不伪造 memory 收益。
- Recovery / Resume Ablation：区分 session log primitive 和端到端 resume rows，只测当前已实现的 checkpoint resume 与 orphan tool-call repair。

如果要真实跑 memory on/off 和 resume enabled/disabled 对照，需要显式打开：

```bash
python benchmarks/local-fixture/ablation.py \
  --output-root benchmarks/local-fixture/results \
  --run-name "ablation-real-$(date +%Y%m%d-%H%M%S)" \
  --suite all \
  --timeout 180 \
  --run-memory-ablation \
  --run-resume-ablation \
  --stream
```

消融实验的结果写入 `ablation.json`、`ablation-report.md` 和 `DATA_PROVENANCE.md`。它们和主 benchmark 的 `benchmark.json` 分开，避免把“合同回归”和“模块收益”混成一个总分。

## Suites

默认 suite 是 `core`，共 34 个任务，覆盖日常开发主路径、上下文压力、memory、resume 和 run artifacts。security 任务单独放在 `security` suite 中，因为它们使用显式 deny rule、非法工具参数和 workspace 边界尝试来观察权限/校验事件。permissions suite 覆盖非默认 benchmark 权限模式，例如 `--dont-ask` 和 protected path 在 `--yolo` 下不能被自动批准。

| Suite | 数量 | 说明 |
|------|:---:|------|
| `core` | 34 | 默认主回归集，不含 adversarial security 任务 |
| `security` | 5 | deny rule、无效 patch 参数、workspace 边界、工具校验恢复 |
| `permissions` | 2 | task 级 permission mode、dontAsk 拒绝、yolo protected path 显式确认 |
| `memory` | 3 | 从 core 中筛选 memory 任务 |
| `resume` | 4 | 从 core 中筛选 checkpoint/resume 任务 |
| `all` | 41 | core + security + permissions |

常用命令：

```bash
# 默认 core
python benchmarks/local-fixture/run.py --run-name core

# 只跑一个任务
python benchmarks/local-fixture/run.py --task-id python_clamp --run-name clamp

# 只跑 resume 专项
python benchmarks/local-fixture/run.py --suite resume --run-name resume

# 只跑 security 专项
python benchmarks/local-fixture/run.py --suite security --run-name security

# 只跑 permissions 专项
python benchmarks/local-fixture/run.py --suite permissions --run-name permissions
```

## 当前任务清单

当前 `tasks.json` 一共有 41 个任务：

| ID | 类别 | 预算 | Suite | 评测点 |
|----|------|:---:|-------|--------|
| `readme_intro_locked` | documentation | 6 | core | README 开头句子替换 |
| `readme_schema_note` | documentation | 6 | core | README schema/baseline bullet 替换 |
| `sample_beta_locked` | text-edit | 6 | core | 简单精确替换 |
| `sample_gamma_locked` | text-edit | 6 | core | 简单精确替换 |
| `python_slugify` | python-bugfix | 10 | core | 单文件 Python 字符串逻辑修复 |
| `python_slugify_boundaries` | python-bugfix | 10 | core | Python 字符串归一化边界组合回归 |
| `python_clamp` | python-bugfix | 8 | core | 单文件边界条件修复 |
| `python_parse_bool` | python-bugfix | 10 | core | 异常路径和输入解析修复 |
| `multi_file_python_refactor` | python-bugfix | 12 | core | 跨文件调用链修复 |
| `test_driven_fix` | python-bugfix | 12 | core | 测试驱动修复 |
| `tool_duplicate_second_beta` | tool-boundary | 8 | core | 重复文本中的目标定位 |
| `tool_keep_secret_unchanged` | tool-boundary | 8 | core | 修改目标文件且不误改邻近文件 |
| `tool_workspace_only` | tool-boundary | 8 | core | workspace 内编辑约束 |
| `path_escape_denied_recovery` | tool-boundary | 8 | security | outside-workspace 写入阻断和恢复 |
| `large_file_targeted_edit` | tool-boundary | 8 | core | 长文件目标定位 |
| `huge_file_targeted_edit` | tool-boundary | 8 | core | 大文件目标定位和上下文压力 |
| `context_large_result_persist` | context-governance | 8 | core | 大 pytest 日志落盘和 artifact 引用 |
| `context_tool_history_snip_realistic` | context-governance | 10 | core | 真实中等工具历史触发 Tool History Snip |
| `recovery_config_check` | recovery | 10 | core | 本地检查后修复配置 |
| `recovery_notes_marker` | recovery | 8 | core | 精确行修复 |
| `invalid_edit_recovery` | recovery | 8 | core | 重复文本定位和工具错误恢复 |
| `repeated_read_budget_guard` | recovery | 5 | core | 小工具步数预算和 read_file 次数上限 |
| `json_config_update` | structured-edit | 8 | core | JSON 结构化编辑 |
| `markdown_frontmatter_preserve` | structured-edit | 8 | core | Markdown frontmatter 保留 |
| `resume_orphaned_tool_call` | resume | 8 | core | orphaned tool call 修复和 interrupted run 标记 |
| `resume_checkpoint_goal` | resume | 8 | core | checkpoint goal 恢复 |
| `resume_checkpoint_files` | resume | 8 | core | checkpoint 文件上下文恢复 |
| `resume_hidden_goal` | resume | 8 | core | 当前 prompt 不重复目标时的 checkpoint 恢复 |
| `memory_fact_lookup` | memory | 6 | core | 启动注入 memory 事实召回 |
| `memory_edit_dependency` | memory | 6 | core | memory 作为编辑依赖 |
| `memory_irrelevant_guard` | memory | 8 | core | 无关 memory 不覆盖当前文件事实 |
| `run_artifacts_present` | run-artifacts | 8 | core | run 目录和 report/trace 合同 |
| `trace_contains_tool_events` | run-artifacts | 8 | core | trace 事件合同 |
| `report_tool_metrics` | run-artifacts | 8 | core | report 工具指标合同 |
| `trace_error_recovery` | run-artifacts | 10 | core | 工具错误 trace 与恢复 |
| `security_approval_denied_shell` | security | 8 | security | `.claude/settings.json` deny rule 阻断 shell |
| `security_read_only_write` | security | 8 | security | deny rule 阻断指定 write_file |
| `security_patch_nonunique` | security | 8 | security | `edit_file` 拒绝非唯一 old_string |
| `security_patch_missing_new_text` | security | 8 | security | tool schema 校验缺失必填参数 |
| `permission_dontask_edit_denied` | permissions | 4 | permissions | `--dont-ask` 拒绝需要确认的编辑 |
| `permission_yolo_protected_path_blocked` | permissions | 8 | permissions | `--yolo` 不自动批准 `.env` protected path |

按类别统计：

| 类别 | 数量 | 覆盖能力 |
|------|:---:|----------|
| documentation | 2 | README 文档编辑 |
| text-edit | 2 | 简单文本编辑 |
| python-bugfix | 6 | 单文件、多文件、测试驱动 Python 修复和边界组合回归 |
| tool-boundary | 6 | 精确定位、避免误改、大文件定位 |
| recovery | 4 | 工具错误、本地检查、预算约束后的恢复 |
| structured-edit | 2 | JSON 和 Markdown 结构保留 |
| resume | 4 | checkpoint/resume、orphaned tool call、中断 run 标记、隐藏目标恢复 |
| memory | 3 | 轻量项目 memory 注入和污染防护 |
| context-governance | 2 | 大工具结果落盘、真实工具历史 Snip |
| run-artifacts | 4 | trace/report/patch 审计合同 |
| security | 4 | deny rule 和工具参数校验 |
| permissions | 2 | task 级权限模式和 protected path 回归 |

## 输出结构

真实运行后输出类似：

```text
<output-root>/<run-name>/
├── benchmark.json
├── benchmark-core-report.md
├── DATA_PROVENANCE.md
├── tasks/<task-id>/
│   ├── task_result.json
│   ├── report.json
│   ├── trace.jsonl
│   ├── patch.diff
│   ├── nanocode_stdout.txt
│   ├── nanocode_stderr.txt
│   └── verifier_output.txt
└── workspaces/<task-id>/<fixture_repo>/
```

runner 会从 workspace 中选择本任务对应的主 run 目录复制 artifacts：优先匹配 `trace.jsonl` 的 `run_started.user_request`，并降低 sub-agent run 的优先级；无法匹配时才回退到最新 run。

`benchmark.json` 的 `rows` 只保存判定字段、artifact 路径和 `report_summary`。完整 `report.json`、`trace.jsonl`、`patch.diff` 保存在 `tasks/<task-id>/` 下，便于复盘但不让主 JSON 过度膨胀。

## 评分逻辑

一个任务通过必须同时满足：

- NanoCode 进程退出码为 0。
- verifier 退出码为 0。
- `artifact_path` 指向的预期文件存在。
- `report.json` 存在。
- 配置了 `allowed_tools` 的任务必须有存在且可完整解析的 `trace.jsonl`。
- `tool_steps <= tool_step_budget`。
- `stop_reason == "stop"`。
- 没有不在 task `allowed_tools` 中的工具被成功执行。
- 任务声明的专项能力 contract 通过：
  - security 任务必须配置 `security_expectation`，并在 trace 中匹配具体 tool、input 和 error code。
  - fact/edit memory 任务不能读取 fallback 来源文件。
  - conflict-guard memory 任务必须读取当前事实文件，不能只靠 prompt 或无关 memory 通过。
  - resume 任务必须恢复 session，并标记旧 run interrupted；orphan 子场景还必须修复 orphan tool call。
  - context 任务按标签要求观察到对应上下文治理事件：大结果落盘、Tool History Snip 或 Context Compact。

注意：benchmark 同时记录两类工具白名单指标：

- `allowed_tools_respected`：模型是否请求过不在白名单里的工具。它衡量模型工具选择纪律。
- `allowed_tools_enforced`：不在白名单里的工具是否被成功执行。它衡量 NanoCode runtime 是否正确拦截。

任务总分只要求 `allowed_tools_enforced == true`。如果模型请求了不允许的工具，但 runtime 拒绝了，任务仍可通过；这类行为会进入 tool-control 指标，而不是覆盖最终任务结果。

失败分类由 `run.py` 写入 `failure_category`：

| 分类 | 含义 |
|------|------|
| `nanocode_failed` | NanoCode 进程非 0 退出 |
| `missing_report` | 没有找到主 run `report.json` |
| `invalid_report` | 主 run `report.json` 存在但不是可解析的 JSON 对象 |
| `missing_trace` | 配置了工具白名单的任务没有找到主 run `trace.jsonl` |
| `invalid_trace` | 主 run `trace.jsonl` 存在但不是可完整解析的 JSONL |
| `trace_contract_failed` | trace 可解析但缺少基础运行事件或与 report 工具计数明显不一致 |
| `bad_stop_reason` | stop reason 不是 `stop` |
| `budget_exceeded` | 工具步数超过任务预算 |
| `disallowed_tool_executed` | 不在白名单中的工具被成功执行 |
| `missing_artifact` | 预期 artifact 文件不存在 |
| `verifier_failed` | verifier 未通过 |
| `security_contract_failed` | security 任务没有匹配到期望的具体 tool/input/error 事件 |
| `memory_contract_failed` | memory 任务没有满足对应子能力 contract |
| `resume_contract_failed` | resume 任务没有满足恢复 contract |
| `context_contract_failed` | context 任务没有观察到要求的上下文治理事件 |
| `tool_path_limit_contract_failed` | trace 中指定 tool/path 的调用次数超过任务声明上限 |
| `harness_error` | benchmark harness 自身在单任务执行中抛出异常，已隔离为失败 row |
| `unknown` | 其他未分类失败 |

## 指标含义

`benchmark.json` 包含 `summary`、`rows` 和 `scorecards`。`benchmark-core-report.md` 是从这些字段派生的人类可读摘要。所有指标都来自本地 artifacts，不依赖模型自述。

### Summary

`summary` 是最顶层的运行结果概览，分母通常是本次实际执行的任务数。

| 指标 | 含义 |
|------|------|
| `selected_tasks` | 本次命令选中的任务数量。`--suite all` 时等于 41；`--limit` 或 `--task-id` 会改变它 |
| `executed_tasks` / `total_tasks` | 实际执行并产生 row 的任务数量；dry-run 时为 0 |
| `passed` / `failed` | 按任务总分口径通过/失败的数量 |
| `pass_rate` | `passed / total_tasks` |
| `within_budget` | `tool_steps <= step_budget` 的任务数量 |
| `within_budget_rate` | `within_budget / total_tasks` |
| `verifier_passes` | shell verifier 退出码为 0 的任务数量 |
| `verifier_pass_rate` | `verifier_passes / total_tasks` |
| `avg_tool_steps` | 平均每个任务的工具调用步数 |
| `avg_attempts` | 平均每个任务的模型循环次数 |
| `avg_duration_ms` | 平均任务耗时，单位毫秒 |
| `max_duration_ms` | 单任务最长耗时，单位毫秒 |
| `category_counts` | 按 `category` 聚合的 total/passed/failed/pass_rate/avg_tool_steps |
| `category_avg_tool_steps` | 每个 category 的平均工具步数快捷索引 |
| `failure_category_counts` | 按 `failure_category` 聚合的失败原因数量 |

### Rows

`rows` 是每个任务的单任务观测结果。聚合 scorecards 大多从这些字段和每个任务目录下的 artifacts 派生。

| 字段 | 含义 |
|------|------|
| `id` / `suite` / `category` / `tags` | 任务身份、suite、主类别和标签 |
| `prompt` | 实际传给 NanoCode 的用户请求 |
| `fixture_repo` / `workspace_relpath` | fixture 来源和本次复制出的隔离 workspace |
| `artifact_dir_relpath` | 该任务 artifacts 在结果目录中的位置 |
| `context_window` | 可选的任务级上下文窗口覆盖；仅用于受控上下文压力评测，不改变 provider/model |
| `run_dir_relpath` | NanoCode 主 run 目录位置 |
| `report_relpath` / `trace_relpath` | 复制出的 report/trace 路径 |
| `duration_ms` | NanoCode 执行耗时，单位毫秒 |
| `nanocode_returncode` | NanoCode 进程退出码 |
| `verifier_returncode` / `verifier_passed` | verifier 退出码和是否通过 |
| `report_exists` / `trace_exists` | 是否找到并复制主 run 的 report/trace |
| `report_parse_valid` / `report_parse_error` | report 是否可解析为 JSON 对象；坏 report 会记为 `invalid_report` 而不是中断整轮评测 |
| `trace_parse_valid` / `trace_parse_error` | trace 是否可完整解析；失败时记录 `missing_trace`、非法 JSON 行或非对象行 |
| `trace_event_count` | 可解析 trace 中的事件数量 |
| `trace_contract_required` / `trace_contract_met` | 配置 `allowed_tools` 的任务是否要求 trace，以及 trace 是否满足基础运行合同 |
| `trace_contract_errors` | trace 基础合同失败原因，例如缺少 `run_finished`、缺少 `tool_executed` 或 report 工具名未出现在 trace 中 |
| `expected_artifact_exists` | `artifact_path` 指向的目标文件是否存在 |
| `tool_steps` | report 记录的工具调用步数 |
| `attempts` | report 记录的模型循环次数 |
| `step_budget` | 兼容字段，默认同时作为 `max_turns` 和 `tool_step_budget` 的来源 |
| `max_turns` | 传给 NanoCode `--max-turns` 的模型工具调用轮数上限 |
| `tool_step_budget` / `within_budget` | benchmark 评分使用的工具完成次数预算和是否在预算内 |
| `stop_reason` / `non_failure_stop_reason` | 模型停止原因，以及是否为正常 `stop` |
| `allowed_tools` / `runtime_allowed_tools` | 任务配置和 runtime report 中看到的工具白名单 |
| `requested_tools` | trace 中出现过 `tool_started` 的工具名集合 |
| `successful_tools` | trace 中非 error `tool_executed` 的工具名集合 |
| `used_tools` | report 中工具计数的工具名集合；report 不可用时回退到 requested tools |
| `disallowed_tool_requests` | 模型请求过但不在 task `allowed_tools` 中的工具 |
| `disallowed_tool_executions` | 不在 task `allowed_tools` 中但成功执行了的工具 |
| `allowed_tools_respected` | 是否没有请求白名单外工具；衡量模型工具选择纪律 |
| `allowed_tools_enforced` | 是否没有成功执行白名单外工具；衡量 runtime enforcement |
| `tool_error_codes` | 从 error tool result 中解析出的错误码 |
| `security_case` / `security_event_type` | security 任务的场景和实际观测事件；未匹配具体 expectation 时为 `not_observed` |
| `security_expectation_configured` | 任务是否声明了具体 `security_expectation` |
| `security_expected_tool` / `security_expected_input` / `security_expected_error_code` | security expectation 中声明的具体工具、关键输入和错误码 |
| `security_matched_tool_call` / `security_matched_error_code` / `security_matched_event_type` | trace 是否匹配到期望工具调用及对应错误 |
| `security_contract_met` | security 具体 expectation 是否满足；非 security 任务为 true |
| `memory_task` / `memory_case` | 是否为 memory 任务，以及 memory 子能力：`fact_lookup`、`edit_dependency`、`conflict_guard` |
| `memory_source_path` / `memory_source_read_count` | memory 场景配置的参考文件，以及 trace 中读取该路径的次数 |
| `memory_current_truth_path` / `memory_current_truth_read_count` / `memory_current_truth_read` | conflict guard 场景要求读取的当前事实文件及读取次数 |
| `memory_fallback_source_path` | fact/edit memory 场景中作为 fallback 的来源文件。conflict guard 通常为空，因为读取当前事实不是 fallback |
| `memory_fallback_read_count` / `memory_fallback_read` | trace 中读取 fallback 来源文件的次数，以及是否发生 fallback read |
| `memory_fact_hit` | fact lookup 场景 verifier 通过且没有读取 fallback 来源文件 |
| `memory_edit_dependency_success` | edit dependency 场景 verifier 通过且没有读取 fallback 来源文件 |
| `memory_conflict_guard_passed` | conflict guard 场景 verifier 通过，且 trace 证明读取了当前事实文件 |
| `memory_contract_met` | memory 子能力 contract 是否满足 |
| `context_contract_expected` | 该任务是否要求上下文治理专项事件 |
| `context_expected_large_result_persist` | 该任务是否要求观察到大工具结果落盘 |
| `context_expected_tool_history_snip` | 该任务是否要求观察到 Tool History Snip |
| `context_expected_context_compact` | 该任务是否要求观察到 Context Compact |
| `large_result_persist_count` / `large_result_persist_observed` | 主 run trace 中观察到大工具结果落盘的次数和布尔值 |
| `tool_history_snip_count` / `tool_history_snip_observed` | 主 run trace 中观察到 Tool History Snip 的次数和布尔值 |
| `context_compact_count` / `context_compact_observed` | 主 run trace 中观察到 Context Compact 的次数和布尔值 |
| `context_contract_met` | 上下文治理专项 contract 是否满足 |
| `recovery_case` / `recovery_case_category` | resume/recovery 子场景分类 |
| `resume_is_orphan_case` / `resume_is_checkpoint_case` | resume 任务是否属于 orphan tool call 或 checkpoint 子场景 |
| `resume_expected_status` / `resume_observed_status` | resume 场景期望状态和实际观察状态 |
| `resume_output_restored` | NanoCode 输出中是否观察到 session restored |
| `resume_interrupted_marked` | 预置 running run 是否被标记为 interrupted |
| `resume_orphan_repaired` | orphaned assistant tool call 是否被补 interrupted tool result |
| `resume_session_exists` | resume session log 是否存在 |
| `checkpoint_resume_restore_observed` | checkpoint resume 子场景是否观察到 restore/interrupted/verifier 信号；严格成功率还要求整行 `passed` 和 `resume_contract_met` |
| `resume_contract_met` | resume 专项 contract 是否满足 |
| `specialty_contract_met` / `specialty_failure_category` | 当前任务所有专项 contract 是否满足，以及第一个失败的专项分类 |
| `report_summary` | report 的精简摘要，完整 report 保存在 `tasks/<task-id>/report.json` |
| `passed` / `status` / `failure_category` | 最终任务判定、pass/fail 文本状态和失败分类 |

### Harness Regression

这组指标回答：“本地任务集整体是否仍然能跑通？”

| 指标 | 含义 |
|------|------|
| `task_count` | scorecard 看到的 row 数 |
| `pass_count` / `fail_count` | 通过/失败任务数 |
| `pass_rate` | `pass_count / task_count`，是最终任务通过率 |
| `within_budget_count` / `within_budget_rate` | 工具步数在预算内的任务数和比例 |
| `verifier_pass_count` / `verifier_pass_rate` | verifier 通过数和比例。它只看最终文件/行为，不看工具纪律 |
| `category_counts` | 各任务类别的 total/passed/failed/pass_rate |
| `failure_category_counts` | 失败原因分布，例如 `verifier_failed`、`budget_exceeded` |

如果 `verifier_pass_rate` 高但 `pass_rate` 低，通常说明最终结果改对了，但预算、stop reason、artifact 或 runtime enforcement 之类过程合同失败。

### Tool Control

这组指标回答：“工具调用是否可控，runtime 是否拦住了不该执行的工具？”

| 指标 | 含义 |
|------|------|
| `allowed_tools_checked_count` | 配置了 `allowed_tools` 的任务数量 |
| `allowed_tools_respected_count` | 模型没有请求白名单外工具的任务数量 |
| `allowed_tools_respected_rate` | `allowed_tools_respected_count / allowed_tools_checked_count`。报告中显示为 `allowed_tools_request_respected_rate` |
| `allowed_tools_enforced_count` | 白名单外工具没有被成功执行的任务数量 |
| `allowed_tools_enforced_rate` | `allowed_tools_enforced_count / allowed_tools_checked_count`。这是 runtime enforcement 指标 |
| `disallowed_tool_request_task_count` | 至少请求过一个白名单外工具的任务数量 |
| `disallowed_tool_request_count` | 白名单外工具请求种类总数，来自 `tool_started` trace |
| `disallowed_tool_execution_task_count` | 至少成功执行过一个白名单外工具的任务数量 |
| `disallowed_tool_execution_count` | 白名单外工具成功执行种类总数，来自非 error 的 `tool_executed` trace |
| `avg_tool_steps` | 平均工具步数 |
| `max_tool_steps` | 单任务最大工具步数 |
| `avg_attempts` | 平均模型循环次数 |
| `tool_name_counts` | 各工具在 `report.json.metrics.tool_name_counts` 中的调用次数总和 |
| `tool_error_count` | 所有任务工具错误总数 |
| `tool_error_task_count` / `tool_error_task_rate` | 出现过工具错误的任务数和比例 |
| `runtime_error_count` | runtime error 总数，不包括预期工具错误 |
| `runtime_error_task_count` / `runtime_error_task_rate` | 出现 runtime error 的任务数和比例 |
| `approval_request_count` | 触发 permission confirmation 的次数。core/security 默认使用 `--yolo`，permissions suite 会覆盖部分任务的权限模式 |
| `tool_boundary_task_count` | `category == "tool-boundary"` 的任务数 |
| `tool_boundary_pass_count` / `tool_boundary_pass_rate` | tool-boundary 任务通过数和比例 |

例子：模型请求了 `list_files`，但任务只允许 `read_file/edit_file`。如果 runtime 返回 `Action denied`，则 `allowed_tools_respected=false`，但 `allowed_tools_enforced=true`，任务可以继续按最终结果通过。

### Security

这组指标回答：“当前实现的 deny rule 和工具校验事件是否被观察到，任务是否能在拒绝后恢复？”

| 指标 | 含义 |
|------|------|
| `scenario_count` | security 场景种类数，例如 `read_only_write`、`patch_nonunique` |
| `task_count` | security 任务数量 |
| `pass_count` / `pass_rate` | security 任务通过数和比例 |
| `security_event_observed_count` / `security_event_observed_rate` | 成功匹配到期望 security expectation 或旧期望事件的任务数和比例 |
| `security_event_counts` | 已匹配事件类型分布。当前包括 `action_denied` 和 `invalid_patch_blocked` |
| `tool_error_code_counts` | 从 error `tool_executed` 内容解析出的错误码分布 |
| `security_scenario_counts` | security_case 场景分布 |

`action_denied` 表示权限/deny rule 阻断；`invalid_patch_blocked` 表示工具参数或编辑约束阻断，例如非唯一 `old_string` 或缺少 `new_string`。配置了 `security_expectation` 的任务会同时校验工具名、关键输入和错误码，避免被无关工具错误误判为通过。

### Recovery

这组指标回答：“遇到可恢复错误或需要本地检查时，agent 是否能继续完成任务？”

| 指标 | 含义 |
|------|------|
| `recovery_primary_task_count` | `category == "recovery"` 的主 recovery 任务数量 |
| `recovery_primary_pass_count` / `recovery_primary_pass_rate` | 主 recovery 任务通过数和比例 |
| `recovery_capability_task_count` | recovery 能力任务数量，包含 `category == "recovery"` 或 tag 包含 `recovery` 的任务 |
| `recovery_capability_pass_count` / `recovery_capability_pass_rate` | recovery 能力任务通过数和比例 |
| `recovery_within_budget_count` / `recovery_within_budget_rate` | recovery 能力任务预算内完成数和比例 |
| `recovery_avg_tool_steps` | recovery 能力任务平均工具步数 |
| `recovery_tool_error_task_count` | recovery 能力任务中出现工具错误的任务数 |
| `recovery_after_tool_error_pass_count` | 出现工具错误后仍通过的 recovery 能力任务数 |
| `recovery_after_tool_error_pass_rate` | `recovery_after_tool_error_pass_count / recovery_tool_error_task_count`；没有这类任务时显示 N/A |
| `bad_stop_reason_count` | stop reason 不是 `stop` 的任务数量 |
| `failed_or_interrupted_run_count` | report status 或 stop reason 表示 failed/stopped/interrupted 的任务数量 |

例子：`invalid_edit_recovery` 属于主 recovery 任务；`trace_error_recovery` 的主类别是 `run-artifacts`，但带有 `recovery` tag，因此进入 recovery capability 分母。这样不会因为任务跨维度而漏掉“工具错误后恢复”能力。

### Context Governance

这组指标回答：“上下文治理机制是否被触发，长文件/大结果任务是否仍保留当前请求目标？”

| 指标 | 含义 |
|------|------|
| `large_result_persist_count` | trace 中 `tool_executed.payload.metadata.persisted == true` 的次数 |
| `large_result_persist_task_count` | 至少触发一次大结果落盘的任务数 |
| `large_result_persist_observed` / `large_result_persist_coverage` | 是否观察到大结果落盘；`covered` 表示本轮实际触发，`not_triggered` 表示本轮没有触发 |
| `tool_history_snip_count` | 触发 Tool History Snip 的次数，来自 `context_prepared.reason == "tool_history_snip"` 或 `conversation_committed.reason == "tool_history_snip"` |
| `tool_history_snip_task_count` | 至少触发一次 Tool History Snip 的任务数 |
| `tool_history_snip_observed` / `tool_history_snip_coverage` | 是否观察到 Tool History Snip；`not_triggered` 不等于失败，只代表任务没有把上下文推到该阈值 |
| `tool_history_snip_expected_task_count` | 明确声明需要 Snip 的任务数 |
| `tool_history_snip_expected_pass_count` / `tool_history_snip_expected_pass_rate` | 声明需要 Snip 的任务通过数和比例 |
| `context_compact_count` | 触发 Context Compact 的次数，来自 `context_prepared.reason == "context_compact"`、`conversation_committed.reason == "context_compact"` 或 `context_compacted` |
| `context_compact_task_count` | 至少触发一次 Context Compact 的任务数 |
| `context_compact_observed` / `context_compact_coverage` | 是否观察到 Context Compact；`not_triggered` 不等于失败，只代表本轮没有触发 LLM 级压缩 |
| `context_compact_expected_task_count` | 明确声明需要 Compact 的任务数 |
| `context_compact_expected_pass_count` / `context_compact_expected_pass_rate` | 声明需要 Compact 的任务通过数和比例 |
| `context_prepared_task_count` | 出现上下文准备事件的任务数 |
| `large_file_task_count` | tag 包含 `large-file` 的任务数 |
| `large_file_task_pass_count` / `large_file_task_pass_rate` | large-file 任务通过数和比例 |
| `context_stress_task_count` | tag 包含 `context-stress` 或 `large-file` 的任务数 |
| `current_request_preserved_count` / `current_request_preserved_rate` | context-stress 任务最终通过数和比例，用来观察压缩/落盘后是否仍完成当前请求 |

当前任务集中 `context_large_result_persist` 会完整读取一个真实形态的 pytest 全量日志，验证 Level 1 大工具结果落盘，并要求 agent 从日志里的 Required diagnosis line 提取诊断结果。`context_tool_history_snip_realistic` 使用多个中等大小的发布审计文件和受控 `context_window`，要求 trace 中真实出现 Level 2 Tool History Snip，且不把结果落盘伪装成 Snip。Level 3 Context Compact 仍由 `ablation.py` 做确定性验证；主 benchmark 不为了追求 Compact 覆盖而硬造超大 provider 上下文。

### Memory

这组指标回答：“项目本地 memory 是否在当前任务中提供了可用上下文，是否出现 fallback 重读来源文件？”

| 指标 | 含义 |
|------|------|
| `memory_task_count` | memory 任务数量 |
| `memory_pass_count` / `memory_pass_rate` | memory 任务通过数和比例 |
| `memory_fact_case_count` | fact lookup memory 任务数量 |
| `memory_fact_hit_count` / `memory_fact_hit_rate` | fact lookup 场景中 verifier 通过且没有读取 fallback 来源文件的任务数和比例 |
| `memory_edit_dependency_case_count` | edit dependency memory 任务数量 |
| `memory_edit_dependency_success_count` / `memory_edit_dependency_success_rate` | edit dependency 场景中 verifier 通过且没有读取 fallback 来源文件的任务数和比例 |
| `memory_conflict_case_count` | conflict guard memory 任务数量 |
| `memory_conflict_guard_count` / `memory_conflict_guard_rate` | conflict guard 场景通过数和比例，要求最终结果正确且 trace 证明读取了当前事实文件 |
| `memory_fallback_applicable_count` | 适用 fallback-read 统计的 memory 任务数，目前是 fact lookup 和 edit dependency |
| `memory_fallback_read_task_count` / `memory_fallback_read_rate` | 适用 fallback 的任务中实际读取 fallback 来源文件的任务数和比例 |
| `memory_fallback_read_count` | 读取 fallback 来源文件的总次数 |
| `memory_category_counts` | `memory-*` tags 的分布，例如 `fact_lookup`、`edit_dependency`、`irrelevant` |

`memory_fact_hit_rate`、`memory_edit_dependency_success_rate` 和 `memory_conflict_guard_rate` 是三种不同能力。`memory_fallback_read_rate` 只看 fact/edit 场景，因为这两类任务才把 `memory_source_path` 定义为“memory 不可用时的 fallback 来源”。`memory_irrelevant_guard` 的核心是防止无关 memory 覆盖当前文件事实，因此 contract 明确要求读取 `current_truth.txt`；读取当前事实是正确行为，不算 fallback。

### Resume

这组指标回答：“checkpoint/resume 的当前实现合同是否可用？”

| 指标 | 含义 |
|------|------|
| `resume_scenario_count` | scenario 为 `resume` 或 tag 包含 `resume` 的任务数量 |
| `resume_success_count` / `resume_success_rate` | resume 任务通过数和比例 |
| `checkpoint_resume_case_count` | checkpoint resume 子场景数量 |
| `checkpoint_resume_observed_count` / `checkpoint_resume_observed_rate` | checkpoint resume 子场景观察到 session restored、旧 run interrupted、verifier 通过这些恢复信号的数量和比例 |
| `checkpoint_resume_success_count` / `checkpoint_resume_success_rate` | checkpoint resume 子场景同时满足整行 passed、resume contract 和 checkpoint 子场景的数量和比例 |
| `interrupted_run_marked_count` / `interrupted_run_marked_rate` | 旧 running run 被标记为 interrupted 的任务数和比例 |
| `orphaned_tool_call_case_count` | orphaned assistant tool call 子场景数量 |
| `orphaned_tool_call_repaired_count` / `orphaned_tool_call_repaired_rate` | session 中 orphaned assistant tool call 被补 interrupted tool result 的任务数和比例 |

`orphaned_tool_call_repaired_rate` 的分母只包含 orphaned tool call 子场景。例如 3 个 resume 任务里只有 1 个 orphan 场景，修复成功会显示 `1/1`，不是 `1/3`。`interrupted_run_marked_rate` 仍以全部 resume 任务为分母，因为每个 resume fixture 都预置了一个 running run。

### Run Audit

这组指标回答：“评测和排障所需的运行工件是否完整、schema 是否稳定？”

| 指标 | 含义 |
|------|------|
| `task_count` | row 数 |
| `report_exists_count` / `report_exists_rate` | `tasks/<task-id>/report.json` 存在数和比例 |
| `report_parse_valid_count` / `report_parse_valid_rate` | selected report 可解析为 JSON 对象的任务数和比例 |
| `trace_exists_count` / `trace_exists_rate` | `tasks/<task-id>/trace.jsonl` 存在数和比例 |
| `trace_parse_valid_count` / `trace_parse_valid_rate` | selected trace 可完整解析为 JSONL 对象事件的任务数和比例 |
| `trace_contract_met_count` / `trace_contract_met_rate` | 配置工具白名单的任务满足基础 run/tool 事件一致性，或未要求 trace contract 的任务数和比例 |
| `patch_diff_exists_count` / `patch_diff_exists_rate` | `tasks/<task-id>/patch.diff` 存在数和比例 |
| `trace_has_run_started_count` / `trace_has_run_started_rate` | trace 中包含 `run_started` 的任务数和比例 |
| `trace_has_run_finished_count` / `trace_has_run_finished_rate` | trace 中包含 `run_finished` 的任务数和比例 |
| `trace_has_tool_events_count` / `trace_has_tool_events_rate` | trace 中包含 `tool_started` 或 `tool_executed` 的任务数和比例 |
| `report_schema_valid_count` / `report_schema_valid_rate` | report 含必需字段的任务数和比例 |
| `run_state_available_count` / `run_state_available_rate` | report 中可读 run 状态字段完整的任务数和比例 |
| `artifact_complete_count` / `artifact_complete_rate` | report、trace、patch.diff 都存在的任务数和比例 |
| `trace_event_counts` | 所有 trace 事件名的聚合计数 |

这些指标用于确认 benchmark 本身可审计。任务失败时，优先从对应 `tasks/<task-id>/report.json`、`trace.jsonl`、`patch.diff`、`verifier_output.txt` 定位原因。

### Optional Usage

这组指标回答：“本轮评测大概消耗了多少 token 和估算成本？”它们只用于成本观察，不作为核心能力评分。

| 指标 | 含义 |
|------|------|
| `task_count` | 有 usage report 的任务数量 |
| `total_input_tokens` | 所有任务 `usage.input_tokens` 总和 |
| `total_output_tokens` | 所有任务 `usage.output_tokens` 总和 |
| `total_tokens` | input + output token 总和 |
| `avg_input_tokens` | 平均每个任务 input tokens |
| `avg_output_tokens` | 平均每个任务 output tokens |
| `input_cache_hit_tokens` | provider report 中 cache hit tokens 总和 |
| `input_cache_miss_tokens` | provider report 中 cache miss tokens 总和 |
| `total_estimated_cost_usd` | 所有任务估算成本总和 |
| `avg_estimated_cost_usd` | 平均每个任务估算成本 |
| `max_estimated_cost_usd` | 单任务最高估算成本 |

token/cost 依赖 provider 返回的 usage 字段和 NanoCode 的定价配置；不同 provider 或代理 endpoint 的口径可能不同。

`DATA_PROVENANCE.md` 会随每次运行生成，记录报告中的核心指标来自哪个 JSON 字段或 artifact 文件。

## Resume 指标口径

当前只评测 NanoCode 已实现的 checkpoint/resume 合同：

- 可以从 session log 恢复 canonical conversation。
- 旧 running run 会被标记为 interrupted。
- orphaned assistant tool call 会被修复为 interrupted tool result。
- checkpoint 中保存的目标/文件上下文能帮助后续请求完成。

不评测：

- workspace drift/fingerprint 检测。
- tracked file freshness 或 stale reanchor。
- checkpoint schema mismatch 恢复策略。
- partial shell/tool success 的自动安全重试。
- provider stream 中间 token 恢复。

这些能力当前不是 NanoCode 的实现合同，放进 benchmark 会造成结果不可辩护。

## Security 指标口径

当前 security suite 只覆盖 NanoCode 已实现且可稳定观察的机制：

- `.claude/settings.json` deny rule 触发的 action denied。
- outside-workspace `write_file` 尝试触发的 workspace boundary 拒绝。
- `edit_file` 对非唯一 `old_string` 的拒绝。
- tool schema 对缺失必填参数的拒绝。
- deny 或校验失败后 agent 是否能继续完成安全目标文件。

不评测：

- path/symlink/search escape 的完整安全矩阵。当前只覆盖一个具体 outside-workspace write 合同。
- repeated identical tool call prevention。
- 非法 timeout 范围策略。
- 空 delegate task 策略。
- secret leak rate。

这些能力要么当前没有稳定实现合同，要么在 `--yolo` benchmark 模式下无法得到可靠安全结论。

## Memory 指标口径

memory 任务通过 `_write_memory_fixture()` 在隔离 HOME 下写入。runner 使用 NanoCode 的统一 project identity 逻辑计算 memory 目录，并为 fixture workspace 设置 git ceiling，避免临时目录被父级 NanoCode 仓库识别成同一个项目：

```text
<task-home>/.nanocode/projects/<repo-key>/memory/
├── MEMORY.md
└── project.md
```

memory 指标按子能力拆分：fact lookup 和 edit dependency 关注是否无需读取 fallback 来源文件即可完成；conflict guard 关注无关 memory 是否没有覆盖当前文件事实，并要求 trace 证明读取了当前事实文件。这些都是单轮 fixture 观测，不代表 durable memory promotion 或长期记忆晋升。memory on/off 对照属于 `ablation.py` 的 Working Memory Ablation，不进入主 benchmark 总分。

## 任务格式

`tasks.json` 顶层 schema：

```json
{
  "schema_version": 1,
  "description": "...",
  "tasks": []
}
```

每个 task 字段：

| 字段 | 说明 |
|------|------|
| `id` | 全局唯一任务 ID |
| `prompt` | 传给 NanoCode 的用户请求 |
| `fixture_repo` | 相对 `benchmarks/local-fixture/` 的 fixture 目录 |
| `artifact_path` | 任务完成后必须存在的文件路径，相对任务 workspace |
| `step_budget` | 兼容字段；未显式设置时同时作为 `max_turns` 和 `tool_step_budget` 默认值 |
| `max_turns` | 可选；传给 NanoCode `--max-turns` 的模型工具调用轮数上限 |
| `tool_step_budget` | 可选；benchmark 用来判断 `tool_steps` 是否超预算的工具完成次数上限 |
| `context_window` | 可选；runner 为该任务设置 `NANO_CODE_CONTEXT_WINDOW`，用于受控上下文压力场景 |
| `allowed_tools` | 任务级工具白名单；runner 会传 `--allowed-tools`，并分别统计模型是否请求越界工具、runtime 是否成功拦截越界执行 |
| `permission_mode` | 可选；默认 `yolo`。可设为 `default`、`dontAsk`、`acceptEdits`、`yolo` 或 `bypassPermissions`，runner 会映射到对应 CLI flag |
| `tags` | 任务标签，用于分组分析 |
| `expected_artifact` | 人类可读期望结果 |
| `verifier` | shell verifier 命令，在任务 workspace 中执行 |
| `category` | 任务类别 |
| `scenario` | 可选；`resume` 表示 runner 会预置 session log 和 interrupted run 后用 `--resume` 执行 |
| `recovery_case` | 可选；仅用于当前实现的 checkpoint resume 子类 |
| `resume_session_id` | resume 场景固定 session id |
| `resume_interrupted_run_id` | resume 场景预置的未完成 run id |
| `resume_seed_prompt` | resume 场景中断前已接受的用户请求 |
| `resume_old_string` / `resume_new_string` | resume fixture 中预置工具调用或 checkpoint 的目标替换文本 |
| `resume_orphaned_tool_call` | 可选；为 true 时预置 assistant tool_use 但不写 tool_result |
| `security_case` | 可选；当前实现的 security/validation 场景名 |
| `security_setup` | 可选；runner 执行前写 `.claude/settings.json` deny rule |
| `security_expectation` | security 场景必填；具体 security 期望，包含 `event`、`tool`、`input`、`error_code`，用于把期望错误绑定到具体工具调用 |
| `memory_setup` | 可选；runner 执行前写入项目本地 memory |
| `memory_case` | 可选；memory 子能力，支持 `fact_lookup`、`edit_dependency`、`conflict_guard` |
| `memory_source_path` | 可选；memory 场景参考文件。fact/edit 场景默认也作为 fallback 来源文件 |
| `memory_fallback_source_path` | 可选；显式指定 fact/edit 场景的 fallback 来源文件 |

## 添加任务

1. 在 `fixtures/` 下创建一个小型 fixture 目录。
2. 在 `tasks.json` 中新增 task，确保 `id` 唯一。
3. 写确定性的 `verifier`，只检查最终文件/行为，不依赖模型输出文本。
4. 设置合理的 `max_turns` 和 `tool_step_budget`；简单任务可只设置兼容字段 `step_budget`。
5. 设置最小可用 `allowed_tools`，避免任务靠无关工具完成。
6. 设置稳定 `tags`，便于后续统计。
7. 确认任务对应 NanoCode 已实现能力；未实现能力先不要加进 benchmark。
8. 先跑 `--dry-run` 校验 schema 和 fixture 路径，再用 `--task-id <id>` 单独真实执行。

## 复现与审计

`benchmark.json` 会记录：

- git commit sha
- 当前 branch
- 使用的 model
- 选中的 task id
- fixture snapshot hash
- benchmark definition hash，覆盖 `tasks.json`、`run.py`、`artifacts.py`、`contracts.py`、`metrics.py`、`report.py`

stdout/stderr 会做基础 secret redaction，覆盖常见 API key 环境变量和 `sk-*` 样式 token。不要依赖它作为强安全边界；fixture 和 verifier 仍应避免输出真实敏感数据。
