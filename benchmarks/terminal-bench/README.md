# Terminal-Bench Benchmark for NanoCode

## 思路

[Terminal-Bench](https://github.com/harbor-framework/terminal-bench) 专门测试 CLI Agent 在终端中执行复杂任务的能力——编译代码、配置服务、调试问题等。和 SWE-bench 不同，它不考"改代码修 bug"，而是考"在终端里正确操作"。

它通过 [Harbor](https://github.com/harbor-framework/harbor) 框架运行：每个任务运行在独立的 Docker 容器里，Agent 通过 tmux 操作终端完成任务。

要在这个评测上跑 NanoCode，需要解决两件事：

**1. 怎么让 Harbor 认识 NanoCode？** Harbor 有一个 `BaseAgent` 抽象接口——`setup()` 在容器里安装 Agent，`run()` 执行 Agent。写一个适配类 `NanoCodeAgent`，实现这两个方法。

**2. NanoCode 怎么在 Docker 容器里跑？** `setup()` 默认把当前本地 checkout 上传到任务容器，并执行 `pip install -e /tmp/nanocode-src`。只有显式设置 `NANOCODE_REPO_URL` 时才从 GitHub 安装。

整体流程：

```
Harbor 下载任务 → 创建 Docker 容器（带 tmux）
                       ↓
              NanoCodeAgent.setup()：上传本地 NanoCode + pip install -e + 验证 CLI
                       ↓
              NanoCodeAgent.run()：nanocode --yolo "任务描述"
                       ↓
              Harbor 评测任务完成情况 → 生成报告
```

## 评测规模和任务覆盖

当前文档和命令默认使用 Harbor registry 的 `terminal-bench@2.0`。在本机 `terminalbench` 环境里，Harbor registry 返回 89 个任务，来源为 `https://github.com/laude-institute/terminal-bench-2.git` 的 `69671fbaac6d67a7ef0dfec016cc38a64ef7a77c` commit。

统计命令：

```bash
/root/miniconda3/envs/terminalbench/bin/python - <<'PY'
import asyncio
from harbor.registry.client import RegistryClientFactory

async def main():
    client = RegistryClientFactory.create()
    md = await client.get_dataset_metadata("terminal-bench@2.0")
    print(len(md.task_ids), "tasks")
    for task in md.task_ids:
        print(task.path)

asyncio.run(main())
PY
```

规模汇总：

| 项 | 数量 | 说明 |
|------|------:|------|
| Terminal-Bench 2.0 任务数 | 89 | 每个 task 是一个独立 Docker 化终端任务 |
| 领域 / category 数 | 16 | 来自每个 `task.toml` 的 `metadata.category` |
| 验证方式 | 89 套 verifier | 每个任务自带测试或验证脚本，Harbor 统一汇总 reward/result |

领域说明：

| Category | 中文领域 | 主要考察能力 |
|------|------|------|
| `software-engineering` | 软件工程 / 编程实现 | 编译、实现算法或服务、跨语言集成、修复项目代码、构建命令行工具和处理真实工程约束 |
| `system-administration` | 系统管理 / 运维配置 | 配置 Web 服务、QEMU/虚拟机、邮件服务、Git 服务、编译系统组件和处理 Linux 环境问题 |
| `data-science` | 数据科学 / 数据分析 | 处理数据集、检索评测、SQL/query 优化、统计建模、模型推理结果分析和表格/数组处理 |
| `scientific-computing` | 科学计算 | 数值采样、物理/生物数据处理、DNA/protein 相关任务、科学软件栈升级和参数拟合 |
| `security` | 安全 / 密码分析 | 破解压缩包口令、证书生成、漏洞修复、敏感信息清理、HTML/JS 过滤和密码学分析 |
| `debugging` | 调试 / 故障定位 | 分析 crash、构建失败、数据库截断、LaTeX 输出问题和复杂 diff/merge 问题 |
| `file-operations` | 文件处理 | 恢复数据库/二进制文件、解析 G-code、视频中提取动作、大规模文本编辑和 ELF 文件分析 |
| `data-processing` | 数据清洗 / 多源数据处理 | 汇总日志、合并多源数据、解析财务文档、正则提取日志结构 |
| `mathematics` | 数学 / 密码数学 | 特征值计算、线性/差分密码分析、从模型输出反推函数结构 |
| `model-training` | 模型训练 / 模型工具 | 训练 fastText、恢复 PyTorch 模型、统计数据集 token、封装模型 CLI |
| `machine-learning` | 机器学习 / 推理系统 | 批量推理调度、模型/数据分布搜索、Caffe/PyTorch 类任务和性能约束 |
| `data-querying` | 数据查询 | 构造 SPARQL 查询并从结构化知识库中取回正确结果 |
| `games` | 游戏 / 搜索策略 | 棋类局面分析和最佳走法搜索 |
| `optimization` | 优化建模 | 投资组合优化、约束建模和目标函数求解 |
| `personal-assistant` | 个人助理 / 日程约束 | 按约束安排任务、日程或资源分配 |
| `video-processing` | 视频处理 | 处理视频文件、提取信息并生成可验证输出 |

领域分布和具体任务：

| Category | 中文领域 | Tasks | 具体任务 |
|------|------|------:|------|
| `software-engineering` | 软件工程 / 编程实现 | 26 | `build-pmars`, `build-pov-ray`, `cancel-async-tasks`, `circuit-fibsqrt`, `cobol-modernization`, `code-from-image`, `fix-git`, `fix-ocaml-gc`, `git-leak-recovery`, `gpt2-codegolf`, `headless-terminal`, `kv-store-grpc`, `make-doom-for-mips`, `make-mips-interpreter`, `path-tracing`, `path-tracing-reverse`, `polyglot-c-py`, `polyglot-rust-c`, `prove-plus-comm`, `pypi-server`, `regex-chess`, `schemelike-metacircular-eval`, `torch-pipeline-parallelism`, `torch-tensor-parallelism`, `winning-avg-corewars`, `write-compressor` |
| `system-administration` | 系统管理 / 运维配置 | 9 | `compile-compcert`, `configure-git-webserver`, `git-multibranch`, `install-windows-3.11`, `mailman`, `nginx-request-logging`, `qemu-alpine-ssh`, `qemu-startup`, `sqlite-with-gcov` |
| `data-science` | 数据科学 / 数据分析 | 8 | `hf-model-inference`, `mcmc-sampling-stan`, `mteb-leaderboard`, `mteb-retrieve`, `query-optimize`, `reshard-c4-data`, `rstan-to-pystan`, `sam-cell-seg` |
| `scientific-computing` | 科学计算 | 8 | `adaptive-rejection-sampler`, `bn-fit-modify`, `dna-assembly`, `dna-insert`, `modernize-scientific-stack`, `protein-assembly`, `raman-fitting`, `tune-mjcf` |
| `security` | 安全 / 密码分析 | 8 | `break-filter-js-from-html`, `crack-7z-hash`, `filter-js-from-html`, `fix-code-vulnerability`, `openssl-selfsigned-cert`, `password-recovery`, `sanitize-git-repo`, `vulnerable-secret` |
| `debugging` | 调试 / 故障定位 | 5 | `build-cython-ext`, `custom-memory-heap-crash`, `merge-diff-arc-agi-task`, `overfull-hbox`, `sqlite-db-truncate` |
| `file-operations` | 文件处理 | 5 | `db-wal-recovery`, `extract-elf`, `extract-moves-from-video`, `gcode-to-text`, `large-scale-text-editing` |
| `data-processing` | 数据清洗 / 多源数据处理 | 4 | `financial-document-processor`, `log-summary-date-ranges`, `multi-source-data-merger`, `regex-log` |
| `mathematics` | 数学 / 密码数学 | 4 | `feal-differential-cryptanalysis`, `feal-linear-cryptanalysis`, `largest-eigenval`, `model-extraction-relu-logits` |
| `model-training` | 模型训练 / 模型工具 | 4 | `count-dataset-tokens`, `pytorch-model-cli`, `pytorch-model-recovery`, `train-fasttext` |
| `machine-learning` | 机器学习 / 推理系统 | 3 | `caffe-cifar-10`, `distribution-search`, `llm-inference-batching-scheduler` |
| `data-querying` | 数据查询 | 1 | `sparql-university` |
| `games` | 游戏 / 搜索策略 | 1 | `chess-best-move` |
| `optimization` | 优化建模 | 1 | `portfolio-optimization` |
| `personal-assistant` | 个人助理 / 日程约束 | 1 | `constraints-scheduling` |
| `video-processing` | 视频处理 | 1 | `video-processing` |

这些任务不是“修一个 Python issue”的单一形态，而是覆盖真实终端工作流：编译/构建、调试、数据处理、密码和安全分析、模型推理/训练、QEMU/系统配置、数据库恢复、文本/视频处理、科学计算等。每个任务都有自己的 Docker 环境、初始文件、自然语言说明和 verifier。

---

## 第一步：搭建环境

### 1.1 确认 Docker 和 Docker Compose

```bash
docker --version             # 需要 Docker
docker compose version       # 需要 Docker Compose v2
```

如果没有 Docker Compose v2，从 Docker 官方仓库安装：

```bash
sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt-get update && sudo apt-get install docker-compose-plugin
docker compose version
```

### 1.2 配置 Docker 网络（国内环境必做）

Terminal-Bench 的任务镜像托管在 Docker Hub。国内网络需要配置代理或镜像加速。

#### 方式 A：WSL2 + Windows 宿主机 Clash 代理（推荐）

```bash
# Clash 默认混合端口 7890。确保 Windows 上 Clash 开启了 Allow LAN
sudo mkdir -p /etc/systemd/system/docker.service.d
sudo tee /etc/systemd/system/docker.service.d/proxy.conf <<'EOF'
[Service]
Environment="HTTP_PROXY=http://127.0.0.1:7890"
Environment="HTTPS_PROXY=http://127.0.0.1:7890"
Environment="NO_PROXY=localhost,127.0.0.1"
EOF
sudo systemctl daemon-reload
sudo service docker restart
```

#### 方式 B：Docker 镜像加速器

```bash
sudo tee /etc/docker/daemon.json <<'EOF'
{
  "registry-mirrors": [
    "https://docker.1ms.run",
    "https://docker.xuanyuan.me",
    "https://docker.m.daocloud.io",
    "https://mirror.ccs.tencentyun.com"
  ]
}
EOF
sudo service docker restart
```

> 加速器和代理可以同时配置。Terminal-Bench 任务镜像较冷门，代理方式更可靠。

#### 验证网络

```bash
docker pull python:3.9-slim
# 看到 Downloading 或 Image is up to date → 通了
```

### 1.3 创建 Harbor 环境（Python 3.12+ 独立环境）

Harbor 需要 Python 3.12+，NanoCode 主环境是 3.10。创建一个独立环境避免冲突：

```bash
# 创建独立 conda 环境
conda create -n terminalbench python=3.12 -y

# 安装 Harbor（从 GitHub 源码）
conda run -n terminalbench pip install git+https://github.com/harbor-framework/harbor.git -i https://pypi.org/simple/

# 验证
conda run -n terminalbench python3 --version      # → Python 3.12.x
conda run -n terminalbench harbor --help          # → 显示帮助信息
```

Harbor 装好后的路径是 `/root/miniconda3/envs/terminalbench/bin/harbor`。

### 1.4 确保使用当前本地 NanoCode

默认测试当前目录的 NanoCode 源码，不依赖 GitHub 上是否已经 push：

```bash
export PYTHONPATH=/root/EvoCode/nanocode:$PYTHONPATH
export NANOCODE_SOURCE_DIR=/root/EvoCode/nanocode
```

如果必须测试远端仓库，才设置：

```bash
export NANOCODE_REPO_URL=https://github.com/Kellin233/nano_code.git
```

未设置 `NANOCODE_REPO_URL` 时，`agent.py` 会排除 `.git`、`jobs/`、benchmark logs、`benchmarks/API.txt` 后上传本地源码快照。

---

## 第二步：配 API Key

### 使用 DeepSeek（推荐，费用低）

```bash
cat >/tmp/nanocode-bench.env <<'EOF'
OPENAI_API_KEY=你的DeepSeek的key
OPENAI_BASE_URL=https://api.deepseek.com
EOF
```

### 使用 Anthropic

```bash
cat >/tmp/nanocode-bench.env <<'EOF'
ANTHROPIC_API_KEY=sk-ant-xxx
EOF
```

### 使用其他 OpenAI-compatible 模型

```bash
cat >/tmp/nanocode-bench.env <<'EOF'
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=https://你的endpoint/v1
EOF
```

推荐使用临时 `.env` 文件配合 `--env-file`，避免 API key 出现在 shell history 或进程列表中。

---

## 第三步：跑 Benchmark

### 3.1 先跑 1 个任务测试管道

```bash
export PYTHONPATH=/root/EvoCode/nanocode:$PYTHONPATH

# DeepSeek 模型，先只测 agent setup/run，不跑 verifier
/root/miniconda3/envs/terminalbench/bin/harbor run \
    --env-file /tmp/nanocode-bench.env \
    --jobs-dir /tmp/nanocode-tbench-agent-smoke \
    --agent-import-path benchmarks.terminal-bench.agent:NanoCodeAgent \
    --model openai/deepseek-v4-pro \
    --ae 'OPENAI_API_KEY=${OPENAI_API_KEY}' \
    --ae 'OPENAI_BASE_URL=${OPENAI_BASE_URL}' \
    --dataset terminal-bench@2.0 \
    --n-tasks 1 \
    --n-concurrent 1 \
    --agent-setup-timeout-multiplier 3 \
    --disable-verification \
    --yes

# Anthropic 模型
/root/miniconda3/envs/terminalbench/bin/harbor run \
    --env-file /tmp/nanocode-bench.env \
    --jobs-dir /tmp/nanocode-tbench-agent-smoke \
    --agent-import-path benchmarks.terminal-bench.agent:NanoCodeAgent \
    --model anthropic/claude-sonnet-4-6 \
    --ae 'ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}' \
    --dataset terminal-bench@2.0 \
    --n-tasks 1 \
    --n-concurrent 1 \
    --agent-setup-timeout-multiplier 3 \
    --disable-verification \
    --yes
```

这个命令会：
1. Harbor 下载 `terminal-bench@2.0` 数据集
2. 创建一个 Docker 容器
3. 在容器里执行 `NanoCodeAgent.setup()`：上传当前 checkout、`pip install -e /tmp/nanocode-src`、验证 `nanocode --help`
4. 在容器里执行 `NanoCodeAgent.run()`：用 Harbor 的 `--ae` 环境变量运行 `nanocode --yolo --sandbox local --max-turns 30 "任务描述"`
5. 评测完成情况，写入 `jobs/<timestamp>/result.json`

这里的"跑通"指 Harbor 能完成环境创建、agent setup、NanoCode CLI 执行并写出 `result.json`；任务得分可以是 0。agent-only smoke 通过后，去掉 `--disable-verification` 再跑完整 verifier。

### 3.2 跑更多任务

```bash
# 跑 5 个任务
/root/miniconda3/envs/terminalbench/bin/harbor run \
    --env-file /tmp/nanocode-bench.env \
    --jobs-dir benchmarks/terminal-bench/result \
    --agent-import-path benchmarks.terminal-bench.agent:NanoCodeAgent \
    --model openai/deepseek-v4-pro \
    --ae 'OPENAI_API_KEY=${OPENAI_API_KEY}' \
    --ae 'OPENAI_BASE_URL=${OPENAI_BASE_URL}' \
    --dataset terminal-bench@2.0 \
    --n-tasks 5 \
    --n-concurrent 1 \
    --agent-setup-timeout-multiplier 3

# 跑全量
/root/miniconda3/envs/terminalbench/bin/harbor run \
    --env-file /tmp/nanocode-bench.env \
    --jobs-dir benchmarks/terminal-bench/result \
    --agent-import-path benchmarks.terminal-bench.agent:NanoCodeAgent \
    --model openai/deepseek-v4-pro \
    --ae 'OPENAI_API_KEY=${OPENAI_API_KEY}' \
    --ae 'OPENAI_BASE_URL=${OPENAI_BASE_URL}' \
    --dataset terminal-bench@2.0 \
    --agent-setup-timeout-multiplier 3
```

### 3.3 断点续跑和指定任务

Terminal-Bench 走 Harbor，断点续跑用 Harbor 原生命令。`--job-path` 必须指向包含 `config.json` 的具体 job 目录，也就是 `--jobs-dir` 下面的时间戳子目录。

```bash
# 例：续跑一次中断的 job
/root/miniconda3/envs/terminalbench/bin/harbor job resume \
    --job-path benchmarks/terminal-bench/result/2026-06-09__22-15-43
```

`harbor job resume` 会补跑未完成或被标记为取消的 trial。它不是从 NanoCode 进程内部继续生成，而是重新执行未完成的 task/trial。

只跑指定任务或任务子集：

```bash
# 只跑单个 task name
/root/miniconda3/envs/terminalbench/bin/harbor run \
    --env-file /tmp/nanocode-bench.env \
    --agent-import-path benchmarks.terminal-bench.agent:NanoCodeAgent \
    --model openai/deepseek-v4-pro \
    --ae 'OPENAI_API_KEY=${OPENAI_API_KEY}' \
    --ae 'OPENAI_BASE_URL=${OPENAI_BASE_URL}' \
    --dataset terminal-bench@2.0 \
    --include-task-name gpt2-codegolf \
    --n-concurrent 1 \
    --agent-setup-timeout-multiplier 3

# 支持 glob；也可以排除已经不想跑的任务
/root/miniconda3/envs/terminalbench/bin/harbor run \
    --env-file /tmp/nanocode-bench.env \
    --agent-import-path benchmarks.terminal-bench.agent:NanoCodeAgent \
    --model openai/deepseek-v4-pro \
    --ae 'OPENAI_API_KEY=${OPENAI_API_KEY}' \
    --ae 'OPENAI_BASE_URL=${OPENAI_BASE_URL}' \
    --dataset terminal-bench@2.0 \
    --include-task-name 'python-*' \
    --exclude-task-name 'python-legacy-*' \
    --n-concurrent 1 \
    --agent-setup-timeout-multiplier 3
```

Harbor 当前没有类似 `--start-index` 的数字起点参数；Terminal-Bench 的“从任意任务开始”建议用 `--include-task-name`/`--exclude-task-name` 按任务名或 glob 控制。已有 job 中断时优先用 `harbor job resume`。

### 3.4 参数说明

| 参数 | 说明 | 默认值 | 什么时候改 |
|------|------|:--:|------|
| `--agent-import-path` | NanoCode agent 导入路径 | — | 固定不改 |
| `--model` | `provider/model_name` 格式 | — | 换模型时改 |
| `--ae` | 传给 agent 的环境变量（可多次使用） | — | 加 API key 时用 |
| `--dataset` | 数据集名 | — | 固定 `terminal-bench@2.0` |
| `--include-task-name` | 只包含匹配的任务名，支持 glob | 无 | 指定单题或任务子集 |
| `--exclude-task-name` | 排除匹配的任务名，支持 glob | 无 | 跳过已知问题任务 |
| `--n-tasks` | 最多跑几个任务 | 全部 | 测试时设 1，正式跑不设 |
| `--n-concurrent` | 并行任务数 | 4 | 资源有限时设 1 |
| `--n-attempts` | 失败重试次数 | 1 | 网络不稳时设 2-3 |

### 3.5 `--model` 值怎么填

`--model` 格式固定为 `provider/model_name`：

| 你想用的模型 | `--model` 参数 |
|-------------|---------------|
| DeepSeek Chat | `openai/deepseek-chat` |
| GPT-5.5 | `openai/gpt-5.5` |
| Claude Opus 4.6 | `anthropic/claude-opus-4-6` |
| Claude Sonnet 4.6 | `anthropic/claude-sonnet-4-6` |
| Qwen | `openai/qwen-max` |
| GLM | `openai/glm-4` |

`provider` 决定了 `agent.py` 用哪个环境变量：`openai` → `OPENAI_API_KEY` + `OPENAI_BASE_URL`，`anthropic` → `ANTHROPIC_API_KEY`。

---

## 第四步：看结果

```bash
# 列出所有 job
ls -lt jobs/

# 看最近一次结果
cat jobs/$(ls -t jobs/ | head -1)/result.json | python3 -m json.tool | head -30

# Harbor 面板查看
/root/miniconda3/envs/terminalbench/bin/harbor view jobs
```

---

## 怎么调试

### 任务失败看日志

```bash
# 看某次运行的完整日志
cat jobs/<job_id>/<task_id>/trial.log | tail -100

# 看所有失败原因
grep -r "Exception\|Error\|failed" jobs/<job_id>/ --include="*.log"
```

### Docker 网络问题

如果任务失败报 `context deadline exceeded` 或镜像拉取超时，说明 Docker 网络配置有问题。回到第一步的 ["1.2 配置 Docker 网络"](#12-配置-docker-网络国内环境必做) 检查。

简要排查：

```bash
# 测试 Docker Hub 是否可达
docker pull python:3.9-slim
# 如果失败 → 检查代理或加速器配置
# 如果成功 → 网络通了，问题在别处
```

## 适配原理

`agent.py` 实现了 Harbor 框架的 `BaseAgent` 接口：

**`name()` / `version()`**：返回 `"nanocode"` 和版本号。

**`setup(environment)`**：在 Docker 容器内执行——
1. 安装 `python3`/`pip`
2. 默认上传当前本地 checkout 到 `/tmp/nanocode-src` 并 `pip install -e`
3. 如果设置 `NANOCODE_REPO_URL`，改为 `pip install git+...`
4. 验证 `command -v nanocode` 和 `nanocode --help`

**`run(instruction, environment, context)`**：在容器内执行——
1. 从 Harbor `extra_env` 接收 `--ae` 传入的 API 环境变量
2. `nanocode --yolo --sandbox local --max-turns 30 --max-cost 5.00 --model deepseek-v4-pro "任务描述"`
2. 收集 stdout/stderr 存入 context

Harbor 框架负责其余的事情：下载任务数据集、创建 tmux Docker 容器、评测任务完成情况、生成 JSON 报告。

## 已知限制

- 任务镜像托管在 Docker Hub（`registry-1.docker.io`），需要网络可达或镜像加速器
- Harbor 需要 Python 3.12+，必须用独立 conda 环境
- 默认安装本地 checkout；如果设置 `NANOCODE_REPO_URL`，Docker 容器需要能访问 GitHub
- 首次运行需要下载数据集和 Docker 镜像（几 GB），之后有缓存

## 文件说明

| 文件 | 用途 |
|------|------|
| `agent.py` | Harbor agent 适配器（~120 行），实现 BaseAgent 接口 |
| `../swebench/` | SWE-bench 适配器（另一个 benchmark） |
