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

## 评测规模和项目覆盖

当前脚本使用的是 Hugging Face `princeton-nlp/SWE-bench_Lite` 的 `test` split。SWE-bench Lite 官方定义是从完整 SWE-bench 中筛出的 300 个真实 GitHub issue 修复任务；本地 `terminalbench` 环境实际加载结果也是 300 题。

统计命令：

```bash
/root/miniconda3/envs/terminalbench/bin/python - <<'PY'
from collections import Counter
from datasets import load_dataset

ds = load_dataset("princeton-nlp/SWE-bench_Lite", split="test")
repo_counts = Counter(row["repo"] for row in ds)
print(len(ds), "instances")
print(len(repo_counts), "repositories")
for repo, count in sorted(repo_counts.items()):
    print(repo, count)
PY
```

规模汇总：

| 项 | 数量 | 说明 |
|------|------:|------|
| SWE-bench Lite 题目数 | 300 | 每题是一个真实 issue + buggy repo commit + 期望 patch/test |
| 评测项目 / 仓库数 | 12 | 数据集按 GitHub repository 组织 |
| 代码领域 | 约 6 类 | Web/HTTP、科学计算、数据可视化、机器学习、测试/静态分析、文档工具 |

领域说明：

| 领域 | 覆盖 repo | 主要考察能力 |
|------|------|------|
| Web / HTTP 后端 | `django/django`, `pallets/flask`, `psf/requests` | 理解 Web 框架、HTTP 客户端、请求/响应生命周期、重定向、异常处理、ORM 和配置行为 |
| 科学计算 / 符号计算 | `astropy/astropy`, `sympy/sympy`, `pydata/xarray` | 修复数值、符号、单位、坐标、数组索引、数据结构和科学计算 API 的真实缺陷 |
| 数据可视化 | `matplotlib/matplotlib`, `mwaskom/seaborn` | 定位绘图 API、统计图、坐标轴、后端渲染、样式和数据映射问题 |
| 机器学习 | `scikit-learn/scikit-learn` | 修复 estimator、metrics、preprocessing、model selection 等机器学习库行为 |
| 测试 / 静态分析工具 | `pytest-dev/pytest`, `pylint-dev/pylint` | 理解测试收集、fixture、断言输出、lint rule、AST 推断和诊断报告 |
| 文档构建工具 | `sphinx-doc/sphinx` | 修复文档构建、directive、extension、parser、交叉引用和输出格式问题 |

具体评测项目：

| Repo / 项目 | 题数 | 中文说明 | 主要考察内容 |
|------|------:|------|------|
| `astropy/astropy` | 6 | 天文和科学计算 Python 库 | 单位换算、坐标系统、表格、I/O、模型和科学数据结构的真实 bug |
| `django/django` | 114 | Python Web 框架 | ORM 查询、表单、迁移、URL/视图、HTTP request/response、配置和兼容性问题 |
| `matplotlib/matplotlib` | 23 | Python 绘图库 | 坐标轴、artist、backend、渲染、图例、样式、布局和绘图 API 行为 |
| `mwaskom/seaborn` | 4 | 统计可视化库 | 统计图接口、数据分组、颜色/样式映射和 matplotlib 集成问题 |
| `pallets/flask` | 3 | Python Web 微框架 | app/request context、路由、请求处理、配置和扩展交互问题 |
| `psf/requests` | 6 | Python HTTP 客户端 | 重定向、异常封装、hook、session、streaming 和请求准备逻辑 |
| `pydata/xarray` | 5 | 带标签的多维数组库 | Dataset/DataArray、坐标、索引、广播、选择和数据分析 API 行为 |
| `pylint-dev/pylint` | 6 | Python 静态分析工具 | lint rule、AST 推断、错误诊断、配置和报告输出 |
| `pytest-dev/pytest` | 17 | Python 测试框架 | 测试收集、fixture、断言重写、插件、参数化和失败报告 |
| `scikit-learn/scikit-learn` | 23 | 机器学习库 | estimator、metrics、preprocessing、pipeline、model selection 和输入校验 |
| `sphinx-doc/sphinx` | 16 | 文档构建工具 | directive、extension、parser、交叉引用、主题输出和构建配置 |
| `sympy/sympy` | 77 | 符号数学库 | 代数、微积分、矩阵、方程、解析、化简和符号表达式行为 |

注意：SWE-bench Lite 的原始样本没有像 Terminal-Bench 那样的统一 `category` 字段。这里的“领域”是按 repository 的项目性质归纳，正式筛选/运行时仍以 `repo` 和 `instance_id` 为准。

---

## 第一步：搭建环境

### 1.1 确认 Python 和依赖

```bash
/root/miniconda3/envs/terminalbench/bin/python --version
/root/miniconda3/envs/terminalbench/bin/python -m pip install swebench datasets
/root/miniconda3/envs/terminalbench/bin/python -m pip install -e /root/EvoCode/nanocode
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

当前适配器也会读取 `benchmarks/API.txt`，默认使用 `[DeepSeek V4 Anthropic]`。环境变量优先级更高；如果你已经导出了 `OPENAI_API_KEY`/`OPENAI_BASE_URL` 或 `ANTHROPIC_API_KEY`，会覆盖 `API.txt`。

如果本机 `nanocode` 命令曾经装过旧入口，可以临时指定命令，不必改系统 PATH：

```bash
export NANOCODE_CMD="python -m nanocode.cli.main"
```

---

## 第二步：先看看有哪些题（dry-run）

```bash
# 列出 requests 项目的所有 issue
/root/miniconda3/envs/terminalbench/bin/python benchmarks/swebench/run.py --repos psf/requests --dry-run

# 只列前 3 个
/root/miniconda3/envs/terminalbench/bin/python benchmarks/swebench/run.py --repos psf/requests --limit 3 --dry-run
```

输出示例：

```
加载了 5 个 instances
模型: claude-sonnet-4-6, max_turns: 20, timeout: 300s
仓库目录: /root/EvoCode/nanocode/benchmarks/swebench/repos

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
/root/miniconda3/envs/terminalbench/bin/python benchmarks/swebench/run.py \
    --repos psf/requests \
    --limit 1 \
    --model deepseek-v4-pro \
    --max-turns 30 \
    --max-cost 1 \
    --timeout 600 \
    --output /tmp/nanocode-swe-predictions.json
```

输出：

```
[1/1] psf__requests-1963 (psf/requests)
  Cloning psf/requests...
  ✅ 生成 patch (363 字符, 80s)

完成: 1 passed, 0 failed, 0 skipped
预测文件: /tmp/nanocode-swe-predictions.json
日志目录: /tmp/logs
```

这个流程自动做了：
1. `git clone` requests 仓库到 `benchmarks/swebench/repos/`
2. checkout 到 buggy 版本
3. 构造 prompt："You are fixing a bug in the psf/requests repository..."  
4. 运行 `nanocode --yolo --max-turns 20 --max-cost 1.00 "<prompt>"`
5. `git diff HEAD` 生成 unified diff
6. 存入 `--output` 指定的 prediction JSON
7. 把每道题的 NanoCode stdout/stderr 写入日志目录

这里的"跑通"指 benchmark 基础设施完整执行，并生成 prediction 文件和单题日志；分数可以是 0，先不要求模型一定修对题。即使所有题都没有 patch，脚本也会创建空的 prediction JSON，方便确认这次运行确实完成过。

### 3.2 跑更多题

```bash
# requests 全部 5 题（约 $2）
/root/miniconda3/envs/terminalbench/bin/python benchmarks/swebench/run.py --repos psf/requests

# requests + flask（约 6 题，$3）
/root/miniconda3/envs/terminalbench/bin/python benchmarks/swebench/run.py --repos psf/requests pallets/flask

# requests + pytest（约 20 题，$5-8）
/root/miniconda3/envs/terminalbench/bin/python benchmarks/swebench/run.py --repos psf/requests pytest-dev/pytest

# pytest 前 5 题（节省费用）
/root/miniconda3/envs/terminalbench/bin/python benchmarks/swebench/run.py --repos pytest-dev/pytest --limit 5
```

### 3.3 先按 repo 比例跑 50 题，再继续剩余题

如果想先测 SWE-bench Lite 的 50 道题，并且希望这 50 道题保持原始数据集中各 repo 的题量比例，用 `--balanced-limit 50`。它会按当前过滤范围内每个 repo 的题目占比等比例缩放到 50 题，再用最大余数法处理小数配额。

`--balanced-limit` 和普通 `--limit` 二选一使用；前者控制“按 repo 比例选 N 题”，后者只是按当前顺序取前 N 题。

全量 SWE-bench Lite 300 题按比例缩放到 50 题时，当前脚本的分布是：

| Repo | 原始题数 | 50 题比例抽样 |
|------|------:|------:|
| `django/django` | 114 | 19 |
| `sympy/sympy` | 77 | 13 |
| `matplotlib/matplotlib` | 23 | 4 |
| `scikit-learn/scikit-learn` | 23 | 4 |
| `pytest-dev/pytest` | 17 | 3 |
| `sphinx-doc/sphinx` | 16 | 2 |
| `astropy/astropy` | 6 | 1 |
| `psf/requests` | 6 | 1 |
| `pylint-dev/pylint` | 6 | 1 |
| `pydata/xarray` | 5 | 1 |
| `mwaskom/seaborn` | 4 | 1 |
| `pallets/flask` | 3 | 0 |

这是比例抽样，不是保证每个 repo 至少 1 题。`pallets/flask` 原始只有 3/300，占比缩放到 50 题约 0.5 道，所以这批 50 题里可能不会出现；后续全量 `--resume` 时仍会继续跑到它。

先 dry-run 看 50 题分布：

```bash
/root/miniconda3/envs/terminalbench/bin/python benchmarks/swebench/run.py \
    --balanced-limit 50 \
    --selection-file /tmp/swe-lite-balanced-50.txt \
    --output /tmp/nanocode-swe-lite.json \
    --dry-run
```

正式跑这 50 题：

```bash
/root/miniconda3/envs/terminalbench/bin/python benchmarks/swebench/run.py \
    --balanced-limit 50 \
    --selection-file /tmp/swe-lite-balanced-50.txt \
    --model deepseek-v4-pro \
    --max-turns 30 \
    --max-cost 1 \
    --timeout 600 \
    --output /tmp/nanocode-swe-lite.json
```

跑完检查结果后，继续跑剩下的题：

```bash
/root/miniconda3/envs/terminalbench/bin/python benchmarks/swebench/run.py \
    --model deepseek-v4-pro \
    --max-turns 30 \
    --max-cost 1 \
    --timeout 600 \
    --output /tmp/nanocode-swe-lite.json \
    --resume
```

这里的关键是复用同一个 `--output`。第一次跑完的 50 道题已经写入 `/tmp/nanocode-swe-lite.json`，后续 `--resume` 会自动跳过这些已有 patch 的 instance，继续跑剩余题目。

如果想分批跑“剩余题里的下一批按比例 50 题”，可以加 `--exclude-existing`：

```bash
/root/miniconda3/envs/terminalbench/bin/python benchmarks/swebench/run.py \
    --balanced-limit 50 \
    --exclude-existing \
    --model deepseek-v4-pro \
    --max-turns 30 \
    --max-cost 1 \
    --timeout 600 \
    --output /tmp/nanocode-swe-lite.json \
    --resume
```

如果想复现第一次选出的那 50 题，用 `--instance-file` 读取选题文件：

```bash
/root/miniconda3/envs/terminalbench/bin/python benchmarks/swebench/run.py \
    --instance-file /tmp/swe-lite-balanced-50.txt \
    --output /tmp/nanocode-swe-lite.json \
    --dry-run
```

### 3.4 断点续跑和指定起点

`run.py` 开始执行时会先创建 `--output` 指定的 prediction JSON；每生成一道题的 patch 就会立即更新这个文件，所以进程被中断后可以用同一个输出文件续跑。没有生成 patch 的题不会写入 prediction key，但会保留单题日志。

```bash
# 中断后续跑同一批任务：跳过 output 中已有非空 patch 的 instance
/root/miniconda3/envs/terminalbench/bin/python benchmarks/swebench/run.py \
    --repos psf/requests pytest-dev/pytest \
    --model deepseek-v4-pro \
    --output /tmp/nanocode-swe-predictions.json \
    --resume
```

从某个位置开始跑：

```bash
# 跳过 psf/requests 过滤后的前 2 题，从第 3 题开始跑 2 题
/root/miniconda3/envs/terminalbench/bin/python benchmarks/swebench/run.py \
    --repos psf/requests \
    --start-index 2 \
    --limit 2 \
    --output /tmp/nanocode-swe-predictions.json

# 从某个 instance 后面一题开始
/root/miniconda3/envs/terminalbench/bin/python benchmarks/swebench/run.py \
    --repos psf/requests \
    --start-after psf__requests-1963 \
    --limit 2 \
    --output /tmp/nanocode-swe-predictions.json
```

只跑指定 instance：

```bash
/root/miniconda3/envs/terminalbench/bin/python benchmarks/swebench/run.py \
    --instance-ids psf__requests-1963 psf__requests-2148 \
    --output /tmp/nanocode-swe-predictions.json \
    --resume
```

可以先加 `--dry-run --resume` 看哪些题会被跳过：

```bash
/root/miniconda3/envs/terminalbench/bin/python benchmarks/swebench/run.py \
    --repos psf/requests \
    --output /tmp/nanocode-swe-predictions.json \
    --dry-run \
    --resume
```

过滤顺序是：`--repos` → `--start-after`/`--start-index` → `--instance-ids`/`--instance-file` → `--exclude-existing` → `--balanced-limit` 或 `--limit`。通常 `--instance-ids` 和起点参数二选一使用即可。

### 3.5 参数说明

| 参数 | 说明 | 默认值 | 什么时候改 |
|------|------|:--:|------|
| `--repos` | 只跑指定仓库 | 全部 12 个 | 节省费用，聚焦项目 |
| `--instance-ids` | 只跑指定 instance_id，支持空格或逗号分隔 | 无 | 精确重跑某几题 |
| `--instance-file` | 从文本文件读取 instance_id | 无 | 复现某批选题 |
| `--start-index` | 在 repo 过滤后的列表中跳过前 N 题 | 0 | 从中间开始跑 |
| `--start-after` | 从指定 instance_id 后面一题开始跑 | 无 | 已知最后完成的 instance |
| `--limit` | 最多跑 N 个 | 全部 | 快速测试 |
| `--balanced-limit` | 按 repo 原始题量比例选 N 个 instance | 无 | 先跑代表性子集，比如 50 题 |
| `--model` | 模型名 | `NANO_CODE_MODEL` 或 `claude-sonnet-4-6` | 换模型 |
| `--max-turns` | 每道题最多对话轮次 | 20 | 复杂 bug 需要更多 |
| `--max-cost` | 每道题最多估算花费($) | 1.00 | 控制预算；`deepseek-v4-pro` 按 DeepSeek 价格估算 |
| `--timeout` | 每道题超时(秒) | 300 | 慢任务给更长时间 |
| `--clone-timeout` | clone/fetch/checkout 超时(秒) | 60 | 大 repo 或网络慢时调大 |
| `--dry-run` | 只打印，不执行 | false | 先看看有哪些题 |
| `--repos-base` | 仓库 clone 到哪 | `benchmarks/swebench/repos` | 通常不用改 |
| `--output` | prediction JSON 输出路径 | `benchmarks/swebench/predictions.json` | smoke 时写到 `/tmp`，避免覆盖默认结果 |
| `--logs-dir` | NanoCode 单题日志输出目录 | `--output` 所在目录下的 `logs/` | 需要把日志集中到指定目录时使用 |
| `--selection-file` | 把本次筛选后的 instance_id 写入文件 | 无 | 保存按比例选出的 50 题，方便复现 |
| `--exclude-existing` | 筛选阶段排除 output 中已有 patch 的题 | false | 分批跑下一批剩余题 |
| `--quiet` | 不实时滚动 nanocode 对话过程，只输出结果摘要 | false | 批量跑时减少终端噪音 |
| `--resume` | 跳过 output 中已有非空 patch 的 instance | false | 中断后续跑 |
| `--skip-existing` | 同 `--resume` | false | 只想跳过已有结果 |

环境变量：

| 变量 | 说明 |
|------|------|
| `NANOCODE_API_PROFILE` | 从 `benchmarks/API.txt` 选择 profile，默认 `DeepSeek V4 Anthropic` |
| `NANOCODE_CMD` | 覆盖 NanoCode 命令，如 `python -m nanocode.cli.main` |

成本估算说明：NanoCode 会按模型名选择价格表。`deepseek-v4-pro` 当前按 DeepSeek 官方价格估算：input cache hit `$0.003625 / 1M tokens`，input cache miss `$0.435 / 1M tokens`，output `$0.87 / 1M tokens`。如果 API usage 没有返回 cache hit/miss 拆分，输入 token 会保守地全部按 cache miss 计算。

### 3.6 实时输出和日志

默认运行时，每道题的 nanocode 对话过程会**实时滚动到终端**，同时写入单题日志文件。这样你可以看到 Agent 每一步在做什么——读了什么文件、调了什么工具、改了哪行代码。

如果是批量跑（几十上百题），终端滚动太快看不清楚，可以加 `--quiet` 只输出结果摘要：

```bash
/root/miniconda3/envs/terminalbench/bin/python benchmarks/swebench/run.py \
    --repos psf/requests pytest-dev/pytest \
    --quiet
```

`--quiet` 模式终端只输出每道题的最终结果（✅/❌），日志文件照常写入，不影响事后查看。对话日志文件路径会在每道题结果后打印。

### 3.7 切换模型

```bash
# 用 OpenAI-compatible 的 gpt-5.5
/root/miniconda3/envs/terminalbench/bin/python benchmarks/swebench/run.py --repos psf/requests --limit 2 --model gpt-5.5

# 用 Claude Opus
/root/miniconda3/envs/terminalbench/bin/python benchmarks/swebench/run.py --repos psf/requests --limit 2 --model claude-opus-4-6
```

---

## 第四步：跑评测

SWE-bench 评测框架会：拉 Docker 基础镜像 → 给每个 instance 构建独立的 Docker 容器 → apply NanoCode 的 patch → 跑项目的原有测试 → 报告 pass/fail。

```bash
/root/miniconda3/envs/terminalbench/bin/python -m swebench.harness.run_evaluation \
    --dataset_name princeton-nlp/SWE-bench_Lite \
    --predictions_path benchmarks/swebench/predictions.json \
    --run_id nanocode_v1 \
    --namespace none \
    --max_workers 1 \
    --report_dir benchmarks/swebench/result
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

默认日志目录是 `--output` 所在目录下的 `logs/`。例如 `--output /root/EvoCode/nanocode/benchmarks/swebench/result/nanocode-v1-n5.json` 时，日志会写到 `/root/EvoCode/nanocode/benchmarks/swebench/result/logs/`。

```bash
ls -lt /root/EvoCode/nanocode/benchmarks/swebench/result/logs | head
tail -120 /root/EvoCode/nanocode/benchmarks/swebench/result/logs/django__django-10914_*.log
```

如果想显式指定日志目录：

```bash
/root/miniconda3/envs/terminalbench/bin/python benchmarks/swebench/run.py \
    --balanced-limit 5 \
    --output /root/EvoCode/nanocode/benchmarks/swebench/result/nanocode-v1-n5.json \
    --logs-dir /root/EvoCode/nanocode/benchmarks/swebench/result/nanocode-v1-n5-logs
```

### 本地仓库缓存

同一个 repo 的不同 instance 复用同一个本地 clone（`benchmarks/swebench/repos/<repo>`），每个 instance 开始前脚本自动执行 `git checkout -f` + `git clean -fdx` 强制清理，保证仓库状态干净。即使上一次运行中途崩溃，也不会污染下一个 instance。

如果某个本地仓库损坏（比如 `checkout` 失败、`git status` 报大量 deleted 文件），直接删掉对应目录即可，下次运行会重新 clone：

```bash
rm -rf benchmarks/swebench/repos/mwaskom__seaborn
```

也可以用一条命令把所有已缓存仓库重置到干净状态：

```bash
for dir in benchmarks/swebench/repos/*/; do
  git -C "$dir" checkout -f HEAD
  git -C "$dir" clean -fdx
done
```

### clone 大仓库超时

`django/django`、`matplotlib/matplotlib`、`scikit-learn/scikit-learn` 这类仓库比较大，首次 clone 可能超过默认时间。脚本默认 `--clone-timeout 60`，如果网络较慢可以临时调大：

```bash
/root/miniconda3/envs/terminalbench/bin/python benchmarks/swebench/run.py \
    --balanced-limit 5 \
    --clone-timeout 1200 \
    --output /tmp/nanocode-swe-lite.json
```

如果之前 clone 被中断，脚本会自动删除不完整的 repo 目录并在下次重试。也可以手动清理单个缓存仓库：

```bash
rm -rf benchmarks/swebench/repos/django__django
```

### 手动验证某道题的 patch

```bash
# 找到仓库
ls benchmarks/swebench/repos/

# 手动 apply NanoCode 的 patch
cd benchmarks/swebench/repos/psf__requests
git apply /dev/stdin <<'PATCH'
... (从 predictions.json 复制 model_patch 内容)
PATCH

# 跑测试
python3 -m pytest tests/test_xxx.py -x
```

### 重跑某道题

推荐直接指定 instance，并用同一个输出文件覆盖该题结果：

```bash
/root/miniconda3/envs/terminalbench/bin/python benchmarks/swebench/run.py \
    --instance-ids psf__requests-1963 \
    --output benchmarks/swebench/predictions.json
```

如果只是断点续跑，不想覆盖已有结果，加 `--resume`。如果一定要强制重新生成某题，可以删除 `predictions.json` 中对应 key 后再用 `--resume` 跑。

---

## 文件说明

| 文件 | 用途 |
|------|------|
| `run.py` | 适配脚本（~790 行）：load instance → clone → run nanocode（实时流式输出）→ git diff → 输出 prediction JSON |
| `predictions.json` | 生成的预测文件，SWE-bench 标准格式 |
| `logs/` | 每道题 NanoCode 运行的完整 stdout/stderr；默认位于 `--output` 同级目录 |
