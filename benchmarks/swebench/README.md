# SWE-bench Lite Benchmark for NanoCode

## 是什么

[SWE-bench Lite](https://www.swebench.com/lite.html) 是 AI 编程 Agent 的标准评测集——300 个真实 Python 项目的 bug fix issue。Agent 拿到 issue 描述，在代码库中定位 bug，生成 patch，跑测试验证。

NanoCode 的适配器 `run.py` 自动完成以下流程：载入 issue → clone 仓库 → checkout buggy 版本 → 调用 `nanocode --yolo` 修复 → `git diff` 生成 patch → 输出 `predictions.json` → 调用 SWE-bench 评测框架跑测试验证。

## 环境要求

- Python 3.10+（已安装 `swebench` 和 `datasets`）
- Docker（用于评测阶段的隔离环境）
- LLM API key（Anthropic 或 OpenAI-compatible）

```bash
pip install swebench datasets
```

## 使用流程

### 1. Dry-run（先看看有哪些题）

```bash
python3 benchmarks/swebench/run.py --repos psf/requests pallets/flask --dry-run
```

输出会列出所有匹配的 issue 标题和 repo，不实际执行。

### 2. 跑 Benchmark

```bash
export ANTHROPIC_API_KEY=你的key
# 或 OpenAI-compatible：
# export OPENAI_API_KEY=你的key
# export OPENAI_BASE_URL=你的endpoint

# 跑 requests 和 flask 的所有题目（~6 题，约 $2，15 分钟）
python3 benchmarks/swebench/run.py --repos psf/requests pallets/flask

# 跑 pytest 的前 10 题
python3 benchmarks/swebench/run.py --repos pytest-dev/pytest --limit 10

# 跑全部 300 题（$30-50，几小时）
python3 benchmarks/swebench/run.py
```

**参数说明**：

| 参数 | 说明 | 默认值 |
|------|------|:--:|
| `--repos` | 只跑指定仓库 | 跑全部 |
| `--limit` | 最多跑 N 个 instance | 全部 |
| `--model` | 模型名 | `NANO_CODE_MODEL` 环境变量 或 `claude-sonnet-4-6` |
| `--max-turns` | 每个任务最多对话轮次 | 20 |
| `--max-cost` | 每个任务最多花费（美元） | 1.00 |
| `--timeout` | 每个任务超时（秒） | 300 |
| `--dry-run` | 只打印不执行 | false |

输出文件：`benchmarks/swebench/predictions.json`（SWE-bench 标准格式）。

日志：`benchmarks/swebench/logs/`（每个 instance 的 nanocode 完整输出）。

### 3. 评测（跑 Docker 测试验证 patch）

```bash
python3 -m swebench.harness.run_evaluation \
    --dataset_name princeton-nlp/SWE-bench_Lite \
    --predictions_path benchmarks/swebench/predictions.json \
    --run_id nanocode_v1 \
    --namespace none \
    --max_workers 1
```

评测会为每个 instance 创建 Docker 容器，apply patch，运行项目原有的测试。输出一个 JSON 报告，包含 `resolved`（通过）和 `unresolved`（未通过）计数。

**注意**：评测需要 Docker Hub 可访问（SWE-bench 会构建 Docker 镜像，基础镜像来自 Docker Hub）。如果配了 Docker 镜像加速器，SWE-bench 可以正常工作——它是本地构建镜像，不是远程拉取。

### 4. 解读结果

```bash
python3 -c "
import json
with open('nanocode-gpt-5.5.nanocode_v1.json') as f:
    d = json.load(f)
print(f'通过: {d[\"resolved_instances\"]}')
print(f'未通过: {d[\"unresolved_instances\"]}')
print(f'错误: {d[\"error_instances\"]}')
print(f'通过率: {d[\"resolved_instances\"] / d[\"submitted_instances\"] * 100:.1f}%')
"
```

## 完整流程示例

```bash
# Step 1: 查看有多少题
python3 benchmarks/swebench/run.py --repos psf/requests --dry-run

# Step 2: 跑 2 题测试管道
export ANTHROPIC_API_KEY=sk-ant-xxx
python3 benchmarks/swebench/run.py --repos psf/requests --limit 2

# Step 3: 评测
python3 -m swebench.harness.run_evaluation \
    --dataset_name princeton-nlp/SWE-bench_Lite \
    --predictions_path benchmarks/swebench/predictions.json \
    --run_id test --namespace none --max_workers 1

# Step 4: 看结果
ls nanocode-gpt-5.5.test.json
```

## 关键文件

| 文件 | 说明 |
|------|------|
| `run.py` | SWE-bench 适配脚本（~150 行） |
| `predictions.json` | 生成的预测文件（SWE-bench 标准格式） |
| `logs/` | 每次 nanocode 运行的完整 stdout/stderr |
