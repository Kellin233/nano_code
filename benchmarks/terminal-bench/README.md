# Terminal-Bench Benchmark for NanoCode

## 思路

[Terminal-Bench](https://github.com/harbor-framework/terminal-bench) 专门测试 CLI Agent 在终端中执行复杂任务的能力——编译代码、配置服务、调试问题等。和 SWE-bench 不同，它不考"改代码修 bug"，而是考"在终端里正确操作"。

它通过 [Harbor](https://github.com/harbor-framework/harbor) 框架运行：每个任务运行在独立的 Docker 容器里，Agent 通过 tmux 操作终端完成任务。

要在这个评测上跑 NanoCode，需要解决两件事：

**1. 怎么让 Harbor 认识 NanoCode？** Harbor 有一个 `BaseAgent` 抽象接口——`setup()` 在容器里安装 Agent，`run()` 执行 Agent。写一个适配类 `NanoCodeAgent`，实现这两个方法。

**2. NanoCode 怎么在 Docker 容器里跑？** NanoCode 不在 PyPI 上，需要从 GitHub 安装。`setup()` 在容器内执行 `pip install git+https://github.com/Kellin233/nano_code.git`。

整体流程：

```
Harbor 下载任务 → 创建 Docker 容器（带 tmux）
                       ↓
              NanoCodeAgent.setup()：pip install nanocode + 设环境变量
                       ↓
              NanoCodeAgent.run()：nanocode --yolo "任务描述"
                       ↓
              Harbor 评测任务完成情况 → 生成报告
```

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

### 1.4 确保 NanoCode 仓库可公开访问

`agent.py` 在 Docker 容器内通过 GitHub 安装 NanoCode：

```bash
pip install git+https://github.com/Kellin233/nano_code.git
```

所以需要：
- NanoCode 已推到 GitHub（`Kellin233/nano_code`）
- Docker 容器能访问 GitHub

如果 GitHub 不可用，需要用其他方式（见"常见问题"）。

---

## 第二步：配 API Key

### 使用 DeepSeek（推荐，费用低）

```bash
export OPENAI_API_KEY=你的DeepSeek的key
export OPENAI_BASE_URL=https://api.deepseek.com   # 或用你的中转地址
```

### 使用 Anthropic

```bash
export ANTHROPIC_API_KEY=sk-ant-xxx
```

### 使用其他 OpenAI-compatible 模型

```bash
export OPENAI_API_KEY=sk-xxx
export OPENAI_BASE_URL=https://你的endpoint/v1
```

---

## 第三步：跑 Benchmark

### 3.1 先跑 1 个任务测试管道

```bash
export PYTHONPATH=/root/EvoCode/nanocode:$PYTHONPATH

# DeepSeek 模型
/root/miniconda3/envs/terminalbench/bin/harbor run \
    --agent-import-path benchmarks.terminal-bench.agent:NanoCodeAgent \
    --model openai/deepseek-chat \
    --ae OPENAI_API_KEY="${OPENAI_API_KEY}" \
    --ae OPENAI_BASE_URL="${OPENAI_BASE_URL}" \
    --dataset terminal-bench@2.0 \
    --n-tasks 1

# Anthropic 模型
/root/miniconda3/envs/terminalbench/bin/harbor run \
    --agent-import-path benchmarks.terminal-bench.agent:NanoCodeAgent \
    --model anthropic/claude-sonnet-4-6 \
    --ae ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}" \
    --dataset terminal-bench@2.0 \
    --n-tasks 1
```

这个命令会：
1. Harbor 下载 `terminal-bench@2.0` 数据集
2. 创建一个 Docker 容器
3. 在容器里执行 `NanoCodeAgent.setup()`：`pip install git+https://github.com/Kellin233/nano_code.git` + 设环境变量
4. 在容器里执行 `NanoCodeAgent.run()`：`nanocode --yolo --max-turns 30 "任务描述"`
5. 评测完成情况，写入 `jobs/<timestamp>/result.json`

### 3.2 跑更多任务

```bash
# 跑 5 个任务
/root/miniconda3/envs/terminalbench/bin/harbor run \
    --agent-import-path benchmarks.terminal-bench.agent:NanoCodeAgent \
    --model openai/deepseek-chat \
    --ae OPENAI_API_KEY="${OPENAI_API_KEY}" \
    --ae OPENAI_BASE_URL="${OPENAI_BASE_URL}" \
    --dataset terminal-bench@2.0 \
    --n-tasks 5

# 跑全量
/root/miniconda3/envs/terminalbench/bin/harbor run \
    --agent-import-path benchmarks.terminal-bench.agent:NanoCodeAgent \
    --model openai/deepseek-chat \
    --ae OPENAI_API_KEY="${OPENAI_API_KEY}" \
    --ae OPENAI_BASE_URL="${OPENAI_BASE_URL}" \
    --dataset terminal-bench@2.0
```

### 3.3 参数说明

| 参数 | 说明 | 默认值 | 什么时候改 |
|------|------|:--:|------|
| `--agent-import-path` | NanoCode agent 导入路径 | — | 固定不改 |
| `--model` | `provider/model_name` 格式 | — | 换模型时改 |
| `--ae` | 传给 agent 的环境变量（可多次使用） | — | 加 API key 时用 |
| `--dataset` | 数据集名 | — | 固定 `terminal-bench@2.0` |
| `--n-tasks` | 最多跑几个任务 | 全部 | 测试时设 1，正式跑不设 |
| `--n-concurrent` | 并行任务数 | 4 | 资源有限时设 1 |
| `--n-attempts` | 失败重试次数 | 1 | 网络不稳时设 2-3 |

### 3.4 `--model` 值怎么填

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
1. `pip install git+https://github.com/Kellin233/nano_code.git` 安装 NanoCode
2. 解析 `--model openai/deepseek-chat` → 设 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`NANO_CODE_MODEL`

**`run(instruction, environment, context)`**：在容器内执行——
1. `nanocode --yolo --max-turns 30 --max-cost 5.00 --model deepseek-chat "任务描述"`
2. 收集 stdout/stderr 存入 context

Harbor 框架负责其余的事情：下载任务数据集、创建 tmux Docker 容器、评测任务完成情况、生成 JSON 报告。

## 已知限制

- 任务镜像托管在 Docker Hub（`registry-1.docker.io`），需要网络可达或镜像加速器
- Harbor 需要 Python 3.12+，必须用独立 conda 环境
- NanoCode 通过 GitHub 安装，Docker 容器需要能访问 GitHub
- 首次运行需要下载数据集和 Docker 镜像（几 GB），之后有缓存

## 文件说明

| 文件 | 用途 |
|------|------|
| `agent.py` | Harbor agent 适配器（~120 行），实现 BaseAgent 接口 |
| `../swebench/` | SWE-bench 适配器（另一个 benchmark） |
