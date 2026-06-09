# SWE-bench Lite Benchmark for NanoCode

## 思路

SWE-bench Lite 是 AI 编程 Agent 的标准评测集：300 个真实 Python 项目的 bug fix issue。Agent 拿到 issue 描述 → 在代码库中定位 → 改代码修 bug → 跑测试验证。

要在这个评测上跑 NanoCode，需要解决两件事：

**1. 怎么让 NanoCode 自动跑 300 道题？** NanoCode 是交互式 CLI，但评测需要程序化调用。解决方案：用一次性模式（`nanocode --yolo "prompt"`），写一个 Python 脚本循环调用，每道题结束后 `git diff` 抓取 NanoCode 的修改。

**2. 怎么验证 NanoCode 的修改是否正确？** SWE-bench 自带 Docker 评测框架：把 NanoCode 生成的 patch apply 到代码库 → 在 Docker 容器里跑项目原有的测试 → 测试通过 = 修对了。

整体流程：

```
SWE-bench issue → 构造 prompt → nanocode --yolo 修 bug
                                   ↓
                              git diff 抓 patch
                                   ↓
                         Docker 容器 apply patch + 跑测试
                                   ↓
                              通过 / 不通过
```

---

## 第一步：搭建环境

### 1.1 确认 Python 和依赖

```bash
python3 --version                          # 需要 3.10+
pip install swebench datasets              # SWE-bench 评测框架
```

### 1.2 确认 Docker 和 Docker Compose

```bash
docker --version        # 需要 Docker
docker compose version  # 需要 Docker Compose v2（SWE-bench 评测用）
```

如果没有 Docker Compose v2，从 Docker 官方仓库安装：

```bash
sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt-get update && sudo apt-get install docker-compose-plugin
docker compose version
```

### 1.3 配置 Docker 网络（国内环境必做）

SWE-bench 评测阶段需要从 Docker Hub 拉取基础镜像（如 `python:3.9-slim`）。国内网络可能无法直接访问 Docker Hub，需要配置代理或镜像加速。

#### 方式 A：WSL2 + Windows 宿主机 Clash 代理

```bash
# 1. 确认代理端口（Clash 默认混合端口 7890）
# 2. Docker 通过 127.0.0.1 走 WSL2 镜像网络到 Windows 代理
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

> 加速器和代理可以同时配置——加速器优先，未缓存的镜像走代理。

#### 验证网络

```bash
docker pull python:3.9-slim
# 看到 Downloading 或 Image is up to date → 通了
```

如果 Docker 拉不了镜像，配加速器：

```bash
sudo tee /etc/docker/daemon.json <<'EOF'
{
  "registry-mirrors": [
    "https://docker.1ms.run",
    "https://docker.xuanyuan.me",
    "https://mirror.ccs.tencentyun.com"
  ]
}
EOF
sudo service docker restart
```

### 1.3 确认 API Key

```bash
# Anthropic（二选一）
export ANTHROPIC_API_KEY=sk-ant-xxx

# 或 OpenAI-compatible（二选一）
export OPENAI_API_KEY=sk-xxx
export OPENAI_BASE_URL=https://你的endpoint/v1
```

---

## 第二步：先看看有哪些题（dry-run）

```bash
# 列出 requests 项目的所有 issue
python3 benchmarks/swebench/run.py --repos psf/requests --dry-run

# 只列前 3 个
python3 benchmarks/swebench/run.py --repos psf/requests --limit 3 --dry-run
```

输出示例：

```
加载了 5 个 instances
模型: claude-sonnet-4-6, max_turns: 20, timeout: 300s
仓库目录: /tmp/swebench-repos

  [psf__requests-1963] psf/requests — Session.resolve_redirects copies...
  [psf__requests-2148] psf/requests — socket.error exception not caught...
  ...
总共 5 题 (dry-run 模式，未执行)
```

这一步只打印不执行——让你确认数据集加载正常、issue 标题能正确读取。

---

## 第三步：跑 Benchmark

### 3.1 先跑 1 题测试管道

```bash
python3 benchmarks/swebench/run.py --repos psf/requests --limit 1
```

输出：

```
[1/1] psf__requests-1963 (psf/requests)
  Cloning psf/requests...
  ✅ 生成 patch (363 字符, 80s)

完成: 1 passed, 0 failed, 0 skipped
预测文件: benchmarks/swebench/predictions.json
```

这个流程自动做了：
1. `git clone` requests 仓库到 `/tmp/swebench-repos/`
2. checkout 到 buggy 版本
3. 构造 prompt："You are fixing a bug in the psf/requests repository..."  
4. 运行 `nanocode --yolo --max-turns 20 --max-cost 1.00 "<prompt>"`
5. `git diff HEAD` 生成 unified diff
6. 存入 `predictions.json`

### 3.2 跑更多题

```bash
# requests 全部 5 题（约 $2）
python3 benchmarks/swebench/run.py --repos psf/requests

# requests + flask（约 6 题，$3）
python3 benchmarks/swebench/run.py --repos psf/requests pallets/flask

# requests + pytest（约 20 题，$5-8）
python3 benchmarks/swebench/run.py --repos psf/requests pytest-dev/pytest

# pytest 前 5 题（节省费用）
python3 benchmarks/swebench/run.py --repos pytest-dev/pytest --limit 5
```

### 3.3 参数说明

| 参数 | 说明 | 默认值 | 什么时候改 |
|------|------|:--:|------|
| `--repos` | 只跑指定仓库 | 全部 12 个 | 节省费用，聚焦项目 |
| `--limit` | 最多跑 N 个 | 全部 | 快速测试 |
| `--model` | 模型名 | `NANO_CODE_MODEL` 或 `claude-sonnet-4-6` | 换模型 |
| `--max-turns` | 每道题最多对话轮次 | 20 | 复杂 bug 需要更多 |
| `--max-cost` | 每道题最多花费($) | 1.00 | 控制预算 |
| `--timeout` | 每道题超时(秒) | 300 | 慢任务给更长时间 |
| `--dry-run` | 只打印，不执行 | false | 先看看有哪些题 |
| `--repos-base` | 仓库 clone 到哪 | `/tmp/swebench-repos` | 通常不用改 |

### 3.4 切换模型

```bash
# 用 OpenAI-compatible 的 gpt-5.5
python3 benchmarks/swebench/run.py --repos psf/requests --limit 2 --model gpt-5.5

# 用 Claude Opus
python3 benchmarks/swebench/run.py --repos psf/requests --limit 2 --model claude-opus-4-6
```

---

## 第四步：跑评测

SWE-bench 评测框架会：拉 Docker 基础镜像 → 给每个 instance 构建独立的 Docker 容器 → apply NanoCode 的 patch → 跑项目的原有测试 → 报告 pass/fail。

```bash
python3 -m swebench.harness.run_evaluation \
    --dataset_name princeton-nlp/SWE-bench_Lite \
    --predictions_path benchmarks/swebench/predictions.json \
    --run_id nanocode_v1 \
    --namespace none \
    --max_workers 1
```

参数：

| 参数 | 说明 |
|------|------|
| `--dataset_name` | 评测数据集，固定为 `princeton-nlp/SWE-bench_Lite` |
| `--predictions_path` | 第三步生成的预测文件 |
| `--run_id` | 自定义名称，用于输出文件名 |
| `--namespace none` | 不用 Docker Hub namespace（镜像在本地构建） |
| `--max_workers 1` | 并发任务数，第一次跑建议 1 |

评测耗时：每道题 1-3 分钟（构建镜像 + 跑测试）。输出 JSON 报告在当前目录。

### 评测环境可断网运行

SWE-bench 评测是**本地构建 Docker 镜像**——不需要外网（只要基础镜像 `python:3.9-slim` 已拉取）。如果 `python:3.9-slim` 之前通过加速器拉过了，评测完全离线。

---

## 第五步：看结果

```bash
# 找到评测输出（文件名格式：<model>.run_id.json）
ls -t *.json | head -5

# 解读
python3 -c "
import json, glob
files = sorted(glob.glob('*.json'), key=lambda f: 'nanocode' in f, reverse=True)
for f in files[:1]:
    with open(f) as fp:
        d = json.load(fp)
    print(f'文件: {f}')
    print(f'通过: {d[\"resolved_instances\"]}')
    print(f'未通过: {d[\"unresolved_instances\"]}')
    print(f'错误: {d[\"error_instances\"]}')
    total = d['submitted_instances']
    print(f'通过率: {d[\"resolved_instances\"] / total * 100:.1f}%' if total else 'N/A')
"
```

---

## 怎么调试

### 看某道题的 NanoCode 完整输出

```bash
cat benchmarks/swebench/logs/nanocode_*.log | head -100
```

### 手动验证某道题的 patch

```bash
# 找到仓库
ls /tmp/swebench-repos/

# 手动 apply NanoCode 的 patch
cd /tmp/swebench-repos/psf__requests
git apply /dev/stdin <<'PATCH'
... (从 predictions.json 复制 model_patch 内容)
PATCH

# 跑测试
python3 -m pytest tests/test_xxx.py -x
```

### 重跑某道题

删除 `predictions.json` 中对应 key，重新跑第三步。

---

## 文件说明

| 文件 | 用途 |
|------|------|
| `run.py` | 适配脚本（~150 行）：load instance → clone → run nanocode → git diff → 输出 |
| `predictions.json` | 生成的预测文件，SWE-bench 标准格式 |
| `logs/` | 每次 nanocode 运行的完整 stdout/stderr |
