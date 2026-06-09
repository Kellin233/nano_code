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

#### 使用 DeepSeek 模型

```bash
export OPENAI_API_KEY=你的DeepSeek的key
export OPENAI_BASE_URL=https://api.deepseek.com   # 或用你的中转地址
export PYTHONPATH=/root/EvoCode/nanocode:$PYTHONPATH

/root/miniconda3/envs/terminalbench/bin/harbor run \
    --agent-import-path benchmarks.terminal-bench.agent:NanoCodeAgent \
    --model openai/deepseek-chat \
    --ae OPENAI_API_KEY="${OPENAI_API_KEY}" \
    --ae OPENAI_BASE_URL="${OPENAI_BASE_URL}" \
    --dataset terminal-bench@2.0 \
    --n-tasks 1
```

#### 使用 Anthropic 模型

```bash
export ANTHROPIC_API_KEY=你的key
export PYTHONPATH=/root/EvoCode/nanocode:$PYTHONPATH

/root/miniconda3/envs/terminalbench/bin/harbor run \
    --agent-import-path benchmarks.terminal-bench.agent:NanoCodeAgent \
    --model anthropic/claude-sonnet-4-6 \
    --ae ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}" \
    --dataset terminal-bench@2.0 \
    --n-tasks 1
```

#### 使用其他 OpenAI-compatible 模型

```bash
# 比如 gpt-5.5、qwen、glm 等任何兼容 OpenAI 接口的模型
/root/miniconda3/envs/terminalbench/bin/harbor run \
    --agent-import-path benchmarks.terminal-bench.agent:NanoCodeAgent \
    --model openai/gpt-5.5 \
    --ae OPENAI_API_KEY="${OPENAI_API_KEY}" \
    --ae OPENAI_BASE_URL="${OPENAI_BASE_URL}" \
    --dataset terminal-bench@2.0 \
    --n-tasks 1
```

**`--model` 参数格式**：`provider/model_name`。`provider` 固定为 `openai` 或 `anthropic`。agent 自动解析并设置对应环境变量。

#### 模型映射逻辑

| `--model` 参数 | 实际配置 |
|---------------|---------|
| `openai/deepseek-chat` | `OPENAI_API_KEY` + `OPENAI_BASE_URL` + `NANO_CODE_MODEL=deepseek-chat` |
| `openai/gpt-5.5` | `OPENAI_API_KEY` + `OPENAI_BASE_URL` + `NANO_CODE_MODEL=gpt-5.5` |
| `anthropic/claude-sonnet-4-6` | `ANTHROPIC_API_KEY` + `NANO_CODE_MODEL=claude-sonnet-4-6` |

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

## Docker 任务镜像拉取问题

Terminal-Bench 的任务镜像托管在 Docker Hub（`registry-1.docker.io`），国内网络可能无法直接拉取。几种解决方案：

### 方案 1：换镜像加速器（最快）

尝试覆盖更全的加速器：

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
docker pull alexgshaw/gpt2-codegolf:20251031
```

多试几个加速器，不同的服务缓存范围不同。`docker.1ms.run` 和 `docker.xuanyuan.me` 覆盖率较广。

### 方案 2：配置 HTTP 代理

```bash
sudo mkdir -p /etc/systemd/system/docker.service.d
sudo tee /etc/systemd/system/docker.service.d/proxy.conf <<'EOF'
[Service]
Environment="HTTP_PROXY=http://你的代理地址:端口"
Environment="HTTPS_PROXY=http://你的代理地址:端口"
Environment="NO_PROXY=localhost,127.0.0.1"
EOF
sudo systemctl daemon-reload
sudo service docker restart
docker pull alexgshaw/gpt2-codegolf:20251031
```

### 方案 3：从另一台机器导出镜像

在有 Docker Hub 访问的机器上：

```bash
docker pull alexgshaw/gpt2-codegolf:20251031
docker save alexgshaw/gpt2-codegolf:20251031 -o gpt2-codegolf.tar
# 拷贝 tar 文件到这台机器
```

在这台机器上：

```bash
docker load -i gpt2-codegolf.tar
```

Terminal-Bench 检测到本地已有镜像后会跳过拉取。

### 验证

```bash
docker pull alexgshaw/gpt2-codegolf:20251031
# 看到 Downloading 或 Image is up to date 即成功
```

## 已知限制

- 任务镜像托管在 Docker Hub，需要网络可达或镜像加速器
- Harbor 需要 Python 3.12+，与 NanoCode 主环境（Python 3.10）隔离
- NanoCode 需通过 GitHub 安装到 Docker 容器内

## 关键文件

| 文件 | 说明 |
|------|------|
| `agent.py` | Harbor agent 适配器（~80 行），实现 BaseAgent 接口 |
