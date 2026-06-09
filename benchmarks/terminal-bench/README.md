# Terminal-Bench Benchmark for NanoCode

## 是什么

[Terminal-Bench](https://github.com/harbor-framework/terminal-bench) 专门测试 CLI Agent 在终端中执行复杂任务的能力——编译代码、配置服务、调试问题等。任务在 Docker 容器中运行，Agent 通过 tmux 操作终端。

NanoCode 的适配器 `agent.py` 实现了 Harbor 框架的 `BaseAgent` 接口：`setup()` 在容器中安装 NanoCode，`run()` 调用 `nanocode --yolo` 执行任务。

## 环境要求

- Python 3.12+（harbor 框架要求）
- Docker + Docker Compose v2
- LLM API key（Anthropic 或 OpenAI-compatible）

```bash
# 创建独立环境
conda create -n terminalbench python=3.12 -y

# 安装 harbor（从 GitHub 源码）
conda run -n terminalbench pip install git+https://github.com/harbor-framework/harbor.git -i https://pypi.org/simple/

# 验证
conda run -n terminalbench harbor --help
```

## 使用流程

### 1. 确认环境就绪

```bash
# 检查 harbor 可用
/root/miniconda3/envs/terminalbench/bin/harbor --help

# 检查 Docker Compose
docker compose version
# → Docker Compose version 2.x
```

### 2. 跑 Benchmark

```bash
export ANTHROPIC_API_KEY=你的key
export PYTHONPATH=/root/EvoCode/nanocode:$PYTHONPATH

# 跑 1 个任务测试管道
/root/miniconda3/envs/terminalbench/bin/harbor run \
    --agent-import-path benchmarks.terminal-bench.agent:NanoCodeAgent \
    --model anthropic/claude-sonnet-4-6 \
    --n-attempts 1 \
    --ae ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}" \
    --dataset terminal-bench@2.0 \
    --n-concurrent 1 \
    --n-tasks 1

# 跑全量测试
/root/miniconda3/envs/terminalbench/bin/harbor run \
    --agent-import-path benchmarks.terminal-bench.agent:NanoCodeAgent \
    --model anthropic/claude-sonnet-4-6 \
    --n-attempts 1 \
    --ae ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}" \
    --dataset terminal-bench@2.0 \
    --n-concurrent 4
```

**参数说明**：

| 参数 | 说明 | 默认值 |
|------|------|:--:|
| `--agent-import-path` | NanoCode agent 的 Python 导入路径 | — |
| `--model` | 模型名（格式: `provider/model_name`） | — |
| `--n-attempts` | 每个任务重试次数 | 1 |
| `--ae` | 传给 agent 的环境变量（可多次使用） | — |
| `--dataset` | 数据集名 | `terminal-bench@2.0` |
| `--n-concurrent` | 并行任务数 | 4 |
| `--n-tasks` | 最多跑几个任务 | 全部 |

### 3. 查看结果

```bash
# 列出所有 job
/root/miniconda3/envs/terminalbench/bin/harbor view jobs

# 查看最近一次
ls jobs/*/result.json

# 结合 Harbor 面板可视化
/root/miniconda3/envs/terminalbench/bin/harbor view jobs/<job_id>
```

### 4. 使用 OpenAI-compatible 后端

```bash
export OPENAI_API_KEY=你的key
export OPENAI_BASE_URL=你的endpoint

/root/miniconda3/envs/terminalbench/bin/harbor run \
    --agent-import-path benchmarks.terminal-bench.agent:NanoCodeAgent \
    --model openai/gpt-5.5 \
    --ae OPENAI_API_KEY="${OPENAI_API_KEY}" \
    --ae OPENAI_BASE_URL="${OPENAI_BASE_URL}" \
    --dataset terminal-bench@2.0 \
    --n-tasks 1
```

## 适配原理

`agent.py` 实现了 Harbor 框架的三个关键方法：

**`name()` / `version()`**：返回 `"nanocode"` 和版本号。

**`setup(environment)`**：在 Docker 容器内安装 NanoCode 及其依赖。

**`run(instruction, environment, context)`**：在容器内执行 `nanocode --yolo --max-turns 30 "任务描述"`，收集输出存入 context。

Harbor 框架负责：下载任务数据集 → 创建 Docker 容器（带 tmux）→ 调用 agent 的 `setup()` 和 `run()` → 评测任务完成情况 → 生成报告。

## 已知限制

- 任务镜像托管在 Docker Hub（`registry-1.docker.io`），需要网络可达
- Harbor 需要 Python 3.12+，与 NanoCode 主环境（Python 3.10）隔离
- 当前 agent 没有自定义安装脚本——使用 `pip install nanocode`，需要 NanoCode 在 PyPI 或 GitHub 上可公开访问

## 关键文件

| 文件 | 说明 |
|------|------|
| `agent.py` | Harbor agent 适配器（~80 行），实现 BaseAgent 接口 |
