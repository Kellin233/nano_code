"""SWE-bench Lite 适配器。

用 NanoCode 一次性模式修复 GitHub issue，生成 SWE-bench 需要的 prediction JSON。

用法:
  # 跑 requests 和 flask（~6 题，$1，15 分钟）
  python benchmarks/swebench/run.py --repos psf/requests pallets/flask

  # 跑 requests + pytest（~20 题，$2，30 分钟）
  python benchmarks/swebench/run.py --repos psf/requests pytest-dev/pytest --limit 10

  # 跑全部 Lite（300 题，$30-50，几小时）
  python benchmarks/swebench/run.py

输出:
  benchmarks/swebench/predictions.json
  benchmarks/swebench/logs/
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BENCH_DIR.parent.parent
RESULT_DIR = BENCH_DIR / "result"
OUTPUT_FILE = RESULT_DIR / "predictions.json"
API_FILE = BENCH_DIR.parent / "API.txt"
REPOS_DIR = BENCH_DIR / "repos"

# ─── 配置 ─────────────────────────────────────────

DEFAULT_API_PROFILE = "DeepSeek V4 Anthropic"
DEFAULT_MODEL = os.environ.get("NANO_CODE_MODEL")
DEFAULT_MAX_TURNS = 20
DEFAULT_MAX_COST = 1.0
DEFAULT_TIMEOUT = 300  # 每题的秒数上限
DEFAULT_CLONE_TIMEOUT = 60


@dataclass(frozen=True)
class ApiConfig:
    """Resolved model/API settings for a NanoCode subprocess."""

    provider: str
    model: str
    api_key: str
    api_base: str | None = None
    source: str = "environment"


@dataclass(frozen=True)
class NanoCodeRunResult:
    """Result metadata for one NanoCode subprocess invocation."""

    stdout: str
    log_file: Path
    returncode: int | None = None


def _parse_api_file(path: Path = API_FILE) -> dict[str, dict[str, str]]:
    """Parse benchmarks/API.txt without logging any secret values."""
    if not path.exists():
        return {}

    profiles: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("[") and "]" in line:
            name = line[1 : line.index("]")].strip()
            current = profiles.setdefault(name, {})
            continue
        if current is None:
            continue
        separator = ":" if ":" in line else "=" if "=" in line else None
        if separator is None:
            continue
        key, value = line.split(separator, 1)
        current[key.strip().lower()] = value.strip().strip("\"'")
    return profiles


def _redact(text: str, env: dict[str, str] | None = None) -> str:
    """Redact API keys from logs before writing them to disk."""
    if not text:
        return text
    redacted = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "sk-[redacted]", text)
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"):
        secret = (env or os.environ).get(key)
        if secret and len(secret) >= 8:
            redacted = redacted.replace(secret, "[redacted]")
    return redacted


def _safe_log_stem(instance_id: str) -> str:
    """Return a filesystem-friendly log filename stem."""
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", instance_id).strip("._")
    return stem[:120] or "instance"


def _provider_from_profile(profile_name: str, profile: dict[str, str]) -> str:
    """Infer provider from an API.txt profile without treating every base_url as OpenAI."""
    explicit = (profile.get("provider") or "").strip().lower()
    if explicit in {"anthropic", "openai"}:
        return explicit

    name = profile_name.lower()
    if "anthropic" in name:
        return "anthropic"
    if "openai" in name:
        return "openai"

    base = (profile.get("base_url") or profile.get("base") or "").lower()
    if "anthropic" in base:
        return "anthropic"
    return "openai" if base else "anthropic"


def resolve_api_config(model_override: str | None = None, *, require_key: bool = True) -> ApiConfig | None:
    """Resolve API settings from environment first, then benchmarks/API.txt."""
    model = model_override or os.environ.get("NANO_CODE_MODEL")

    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        return ApiConfig(
            provider="openai",
            model=model or "gpt-4o",
            api_key=openai_key,
            api_base=os.environ.get("OPENAI_BASE_URL"),
            source="environment",
        )

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        return ApiConfig(
            provider="anthropic",
            model=model or "claude-sonnet-4-6",
            api_key=anthropic_key,
            api_base=os.environ.get("ANTHROPIC_BASE_URL"),
            source="environment",
        )

    profile_name = os.environ.get("NANOCODE_API_PROFILE", DEFAULT_API_PROFILE)
    profile = _parse_api_file().get(profile_name)
    if profile:
        key = profile.get("api_key") or profile.get("key") or ""
        base = profile.get("base_url") or profile.get("base") or None
        provider = _provider_from_profile(profile_name, profile)
        if key or not require_key:
            return ApiConfig(
                provider=provider,
                model=model or profile.get("model") or "deepseek-v4-pro",
                api_key=key,
                api_base=base,
                source=f"{API_FILE.name}:{profile_name}",
            )

    if require_key:
        return None
    return ApiConfig(
        provider="anthropic",
        model=model or DEFAULT_MODEL or "claude-sonnet-4-6",
        api_key="",
        source="default",
    )


def build_prompt(instance: dict) -> str:
    """把 SWE-bench instance 转成 NanoCode prompt。"""
    repo = instance["repo"]
    title = instance.get("problem_statement", instance.get("issue_title", ""))
    body = instance.get("hints_text", "")
    if not title:
        title = body.split("\n")[0] if body else "Fix the bug"
    return (
        f"You are fixing a bug in the {repo} repository.\n\n"
        f"## Issue\n{title}\n\n{body}\n\n"
        "## Instructions\n"
        "1. Find the relevant file(s) in this repository.\n"
        "2. Understand the bug described in the issue.\n"
        "3. Make the minimal code edit needed to fix the reported bug before running broad tests.\n"
        "4. Edit ONLY production code related to this issue. Do NOT modify tests.\n"
        "5. After editing, run the narrowest existing test or syntax check that can verify the fix.\n"
        "6. If this historical project fails under the current Python because of unrelated compatibility "
        "issues, report that and keep the patch focused on the issue.\n"
        "7. Do not stop after explaining a possible fix; call the edit tool and leave a non-empty git diff.\n"
        "8. Never call tools with empty arguments. Use concrete file paths and search patterns.\n"
        "9. Do NOT create new files unless absolutely necessary."
    )


def run_nanocode(
    prompt: str,
    cwd: Path,
    instance_id: str,
    logs_dir: Path,
    api_config: ApiConfig,
    max_turns: int,
    max_cost: float,
    timeout: int,
    live_output: bool = True,
) -> NanoCodeRunResult:
    """用一次性模式跑 NanoCode，实时打印对话过程（可关闭），同时持久化到单题日志。"""
    import queue
    import threading

    env = os.environ.copy()
    env["NANO_CODE_MODEL"] = api_config.model
    if api_config.provider == "openai":
        env["OPENAI_API_KEY"] = api_config.api_key
        if api_config.api_base:
            env["OPENAI_BASE_URL"] = api_config.api_base
    else:
        env["ANTHROPIC_API_KEY"] = api_config.api_key
        if api_config.api_base:
            env["ANTHROPIC_BASE_URL"] = api_config.api_base

    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / f"{_safe_log_stem(instance_id)}_{time.time_ns()}.log"
    prefix = f"[{instance_id[-20:]}]"

    nanocode_cmd = shlex.split(os.environ.get("NANOCODE_CMD", "nanocode"))
    cmd = [
        *nanocode_cmd,
        "--yolo",
        "--max-turns",
        str(max_turns),
        "--max-cost",
        str(max_cost),
        "--model",
        api_config.model,
    ]
    if api_config.provider == "openai" and api_config.api_base:
        cmd.extend(["--api-base", api_config.api_base])
    cmd.append(prompt)
    display_cmd = [*cmd[:-1], "<prompt>"]

    # 写 log 文件头
    header = _redact(
        "\n".join(
            [
                f"=== INSTANCE ===\n{instance_id}",
                f"=== CWD ===\n{cwd}",
                f"=== COMMAND ===\n{shlex.join(display_cmd)}",
                "=== OUTPUT ===\n",
            ]
        ),
        env,
    )
    log_file.write_text(header, encoding="utf-8")

    # ── 流式读取 stdout/stderr ──
    line_queue: queue.Queue[tuple[str, str]] = queue.Queue()  # (stream_name, line)

    def _reader(stream_name: str, pipe):
        try:
            for raw_line in iter(pipe.readline, ""):
                if raw_line:
                    line_queue.put((stream_name, raw_line))
        except Exception:
            pass
        finally:
            pipe.close()
            line_queue.put(("__EOF__", stream_name))

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    returncode: int | None = None
    timed_out = False

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(cwd),
            env=env,
        )

        t_stdout = threading.Thread(target=_reader, args=("stdout", process.stdout), daemon=True)
        t_stderr = threading.Thread(target=_reader, args=("stderr", process.stderr), daemon=True)
        t_stdout.start()
        t_stderr.start()

        eof_count = 0
        deadline = time.monotonic() + timeout

        while eof_count < 2:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                timed_out = True
                break

            try:
                stream_name, line = line_queue.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                continue

            if stream_name == "__EOF__":
                eof_count += 1
                continue

            # 实时打印到终端（--quiet 可关闭）
            redacted_line = _redact(line.rstrip("\n"), env)
            if live_output:
                print(f"  {prefix} {redacted_line}")

            # 收集 + 实时写 log
            if stream_name == "stdout":
                stdout_lines.append(line)
            else:
                stderr_lines.append(line)
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(_redact(line, env))

        t_stdout.join(timeout=5)
        t_stderr.join(timeout=5)
        returncode = process.poll()

    except Exception as e:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"\n=== ERROR ===\n{e}\n")
        return NanoCodeRunResult(stdout="", log_file=log_file, returncode=None)

    # 写 log 文件尾
    stdout_full = "".join(stdout_lines)
    footer = _redact(
        "\n".join(
            [
                "\n=== RESULT ===",
                f"RC: {returncode}",
                f"TIMEOUT: {'yes' if timed_out else 'no'}",
            ]
        ),
        env,
    )
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(footer)

    return NanoCodeRunResult(stdout=stdout_full, log_file=log_file, returncode=returncode)


def get_diff(repo_dir: Path) -> str:
    """获取当前工作区的 unified diff。"""
    result = subprocess.run(
        ["git", "-C", str(repo_dir), "diff", "HEAD"],
        capture_output=True,
        text=True,
    )
    return result.stdout


def stash_restore(repo_dir: Path) -> None:
    """恢复 git 仓库到干净状态。"""
    subprocess.run(["git", "-C", str(repo_dir), "checkout", "."], capture_output=True)
    subprocess.run(["git", "-C", str(repo_dir), "clean", "-fdx"], capture_output=True)


def _expand_arg_values(values: list[str] | None) -> list[str] | None:
    """Expand repeated or comma-separated CLI values."""
    if not values:
        return None
    expanded = []
    for value in values:
        expanded.extend(part.strip() for part in value.split(",") if part.strip())
    return expanded or None


def read_instance_file(path: Path) -> list[str]:
    """Read instance IDs from a text file, ignoring blank lines and comments."""
    instance_ids = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        instance_ids.extend(part.strip() for part in re.split(r"[\s,]+", line) if part.strip())
    return instance_ids


def write_selection_file(path: Path, instances: list[dict]) -> None:
    """Write selected instance IDs, one per line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(inst["instance_id"] for inst in instances) + "\n", encoding="utf-8")


def load_predictions(output_file: Path) -> dict:
    """Load an existing SWE-bench predictions JSON file."""
    try:
        with open(output_file, encoding="utf-8") as f:
            output = json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as exc:
        print(f"警告: 无法读取已有 prediction 文件 {output_file}: {exc}", file=sys.stderr)
        return {}
    if not isinstance(output, dict):
        print(f"警告: 已有 prediction 文件不是 JSON object: {output_file}", file=sys.stderr)
        return {}
    return output


def write_predictions(output_file: Path, output: dict) -> None:
    """Atomically write predictions so interrupted runs keep completed work."""
    output_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = output_file.with_name(f".{output_file.name}.tmp")
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp_file.replace(output_file)


def has_existing_prediction(output: dict, instance_id: str) -> bool:
    """Return True when output already has a non-empty patch for an instance."""
    prediction = output.get(instance_id)
    return isinstance(prediction, dict) and bool(str(prediction.get("model_patch", "")).strip())


def repo_distribution(instances: list[dict]) -> dict[str, int]:
    """Return repo -> count in display order."""
    counts: dict[str, int] = {}
    for instance in instances:
        repo = instance["repo"]
        counts[repo] = counts.get(repo, 0) + 1
    return counts


def format_repo_distribution(instances: list[dict]) -> str:
    """Format selected instance distribution for logs."""
    return ", ".join(f"{repo}={count}" for repo, count in repo_distribution(instances).items())


def balanced_select_instances(instances: list[dict], limit: int) -> list[dict]:
    """Select up to limit instances proportionally to each repo's size."""
    if limit <= 0:
        raise ValueError("--balanced-limit 必须大于 0")
    if limit >= len(instances):
        return instances

    groups: dict[str, list[dict]] = {}
    repo_order = []
    for instance in instances:
        repo = instance["repo"]
        if repo not in groups:
            groups[repo] = []
            repo_order.append(repo)
        groups[repo].append(instance)

    repo_index = {repo: i for i, repo in enumerate(repo_order)}
    total = len(instances)
    quotas = []
    selected_counts = {}
    for repo in repo_order:
        exact = len(groups[repo]) * limit / total
        base = int(exact)
        selected_counts[repo] = min(base, len(groups[repo]))
        quotas.append((repo, exact - base))

    remaining = limit - sum(selected_counts.values())
    quotas.sort(key=lambda item: (-item[1], len(groups[item[0]]), repo_index[item[0]]))
    for repo, _remainder in quotas:
        if remaining <= 0:
            break
        if selected_counts[repo] >= len(groups[repo]):
            continue
        selected_counts[repo] += 1
        remaining -= 1

    selected = []
    emitted = dict.fromkeys(repo_order, 0)
    for instance in instances:
        repo = instance["repo"]
        if emitted[repo] < selected_counts[repo]:
            selected.append(instance)
            emitted[repo] += 1

    return selected


def load_instances(
    repos: list[str] | None = None,
    limit: int | None = None,
    balanced_limit: int | None = None,
    instance_ids: list[str] | None = None,
    start_index: int = 0,
    start_after: str | None = None,
    existing_output: dict | None = None,
    exclude_existing: bool = False,
) -> list[dict]:
    """从 SWE-bench Lite 加载 instances，可选过滤 repo、起点和数量。"""
    if start_index < 0:
        raise ValueError("--start-index 不能小于 0")
    if limit and balanced_limit:
        raise ValueError("--limit 和 --balanced-limit 不能同时使用")

    try:
        from datasets import load_dataset

        ds = load_dataset("princeton-nlp/SWE-bench_Lite", split="test")
        instances = []
        for x in ds:
            if repos and not any(r in x["repo"] for r in repos):
                continue
            instances.append(x)
        if start_after:
            for i, instance in enumerate(instances):
                if instance["instance_id"] == start_after:
                    instances = instances[i + 1 :]
                    break
            else:
                raise ValueError(f"--start-after 指定的 instance_id 不存在: {start_after}")
        if start_index:
            instances = instances[start_index:]
        if instance_ids:
            by_id = {x["instance_id"]: x for x in instances}
            seen = set()
            ordered = []
            for instance_id in instance_ids:
                if instance_id in by_id and instance_id not in seen:
                    ordered.append(by_id[instance_id])
                    seen.add(instance_id)
            instances = ordered
        if exclude_existing:
            output = existing_output or {}
            instances = [x for x in instances if not has_existing_prediction(output, x["instance_id"])]
        if balanced_limit:
            instances = balanced_select_instances(instances, balanced_limit)
        if limit:
            instances = instances[:limit]
        return instances
    except ImportError:
        print("需要安装 datasets: pip install datasets", file=sys.stderr)
        return []


def is_git_repo(repo_dir: Path) -> bool:
    """Return True if repo_dir is a usable git working tree."""
    if not repo_dir.exists():
        return False
    result = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def prepare_repo(instance: dict, repos_base: Path, clone_timeout: int) -> Path | None:
    """clone 或准备 SWE-bench instance 对应的仓库。"""
    repo_name = instance["repo"]
    repo_dir = repos_base / repo_name.replace("/", "__")

    if repo_dir.exists() and not is_git_repo(repo_dir):
        print(f"  Removing incomplete repo directory: {repo_dir}")
        shutil.rmtree(repo_dir)

    if not repo_dir.exists():
        print(f"  Cloning {repo_name}...")
        try:
            result = subprocess.run(
                ["git", "clone", "--filter=blob:none", f"https://github.com/{repo_name}.git", str(repo_dir)],
                capture_output=True,
                text=True,
                timeout=clone_timeout,
            )
        except subprocess.TimeoutExpired:
            print(f"  Clone timed out after {clone_timeout}s: {repo_name}")
            if repo_dir.exists():
                shutil.rmtree(repo_dir)
            return None
        if result.returncode != 0:
            print(f"  Clone failed: {result.stderr[:200]}")
            if repo_dir.exists() and not is_git_repo(repo_dir):
                shutil.rmtree(repo_dir)
            return None

    base_commit = instance.get("base_commit", "")
    if base_commit:
        checkout = subprocess.run(
            ["git", "-C", str(repo_dir), "checkout", "-f", base_commit],
            capture_output=True,
            text=True,
            timeout=clone_timeout,
        )
        if checkout.returncode != 0:
            print("  Checkout failed; fetching refs and retrying...")
            try:
                subprocess.run(
                    ["git", "-C", str(repo_dir), "fetch", "--all", "--tags", "--filter=blob:none"],
                    capture_output=True,
                    text=True,
                    timeout=clone_timeout,
                )
                checkout = subprocess.run(
                    ["git", "-C", str(repo_dir), "checkout", "-f", base_commit],
                    capture_output=True,
                    text=True,
                    timeout=clone_timeout,
                )
            except subprocess.TimeoutExpired:
                print(f"  Fetch/checkout timed out after {clone_timeout}s")
                return None
        if checkout.returncode != 0:
            print(f"  Checkout failed: {(checkout.stderr or checkout.stdout)[:300]}")
            return None
        subprocess.run(["git", "-C", str(repo_dir), "clean", "-fdx"], capture_output=True)

    return repo_dir


def main():
    parser = argparse.ArgumentParser(description="SWE-bench Lite adapter for NanoCode")
    parser.add_argument("--repos", nargs="*", default=None, help="只跑指定仓库 (如: psf/requests)")
    parser.add_argument("--instance-ids", nargs="*", default=None, help="只跑指定 instance_id，支持空格或逗号分隔")
    parser.add_argument("--instance-file", default=None, help="从文本文件读取 instance_id，每行一个，也支持逗号/空格分隔")
    parser.add_argument("--start-index", type=int, default=0, help="在 repo 过滤后的列表中跳过前 N 题，从 0 开始")
    parser.add_argument("--start-after", default=None, help="从指定 instance_id 后面一题开始跑")
    parser.add_argument("--limit", type=int, default=None, help="最多跑几个 instance")
    parser.add_argument("--balanced-limit", type=int, default=None, help="按 repo 原始题量比例选 N 个 instance")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="模型 (默认: 环境变量或 API profile)")
    parser.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    parser.add_argument("--max-cost", type=float, default=DEFAULT_MAX_COST)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--clone-timeout", type=int, default=DEFAULT_CLONE_TIMEOUT, help="clone/fetch/checkout 超时秒数")
    parser.add_argument("--repos-base", default=str(REPOS_DIR), help="仓库存储目录")
    parser.add_argument("--output", default=str(OUTPUT_FILE), help="prediction JSON 输出路径")
    parser.add_argument(
        "--logs-dir",
        default=None,
        help="NanoCode 单题日志输出目录；默认使用 --output 所在目录下的 logs/",
    )
    parser.add_argument("--selection-file", default=None, help="把本次筛选后的 instance_id 写入文件，方便复现同一批题")
    parser.add_argument("--exclude-existing", action="store_true", help="筛选阶段排除 output 中已有非空 patch 的 instance")
    parser.add_argument("--skip-existing", action="store_true", help="跳过 output 中已有非空 patch 的 instance")
    parser.add_argument("--resume", action="store_true", help="断点续跑，等同于 --skip-existing；每题完成后会立即写入 output")
    parser.add_argument("--quiet", action="store_true", help="不实时滚动 nanocode 对话过程，只输出结果摘要")
    parser.add_argument("--dry-run", action="store_true", help="只打印要跑的信息，不实际执行")
    args = parser.parse_args()

    api_config = resolve_api_config(args.model, require_key=not args.dry_run)
    if not api_config:
        print("需要设置 ANTHROPIC_API_KEY 或 OPENAI_API_KEY", file=sys.stderr)
        print(f"也可以在 {API_FILE} 中配置 [{DEFAULT_API_PROFILE}]", file=sys.stderr)
        sys.exit(1)
    output_file = Path(args.output)
    logs_dir = Path(args.logs_dir) if args.logs_dir else output_file.parent / "logs"
    output = load_predictions(output_file)

    instance_ids = _expand_arg_values(args.instance_ids) or []
    if args.instance_file:
        instance_ids.extend(read_instance_file(Path(args.instance_file)))
    instance_ids = instance_ids or None
    try:
        instances = load_instances(
            repos=args.repos,
            limit=args.limit,
            balanced_limit=args.balanced_limit,
            instance_ids=instance_ids,
            start_index=args.start_index,
            start_after=args.start_after,
            existing_output=output,
            exclude_existing=args.exclude_existing,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    if not instances:
        print("没有找到匹配的 SWE-bench instances", file=sys.stderr)
        sys.exit(1)

    repos_base = Path(args.repos_base)
    repos_base.mkdir(parents=True, exist_ok=True)
    resume = args.resume or args.skip_existing
    if args.selection_file:
        write_selection_file(Path(args.selection_file), instances)

    print(f"加载了 {len(instances)} 个 instances")
    print(
        f"模型: {api_config.model}, provider: {api_config.provider}, max_turns: {args.max_turns}, timeout: {args.timeout}s"
    )
    print(f"API 配置: {api_config.source}")
    print(f"仓库目录: {repos_base}")
    print(f"预测文件: {output_file}")
    print(f"日志目录: {logs_dir}")
    if args.balanced_limit:
        print(f"比例选题: requested={args.balanced_limit}, selected={len(instances)}")
        print(f"Repo 分布: {format_repo_distribution(instances)}")
    if args.exclude_existing:
        print("筛选阶段: 已排除 output 中已有 prediction 的 instance")
    if args.selection_file:
        print(f"选题文件: {args.selection_file}")
    if resume:
        existing_count = sum(1 for inst in instances if has_existing_prediction(output, inst["instance_id"]))
        print(f"续跑模式: 会跳过 {existing_count} 个已有 prediction 的 instance")
    print()

    if args.dry_run:
        for inst in instances:
            suffix = ""
            if resume and has_existing_prediction(output, inst["instance_id"]):
                suffix = "  [skip-existing]"
            print(f"  [{inst['instance_id']}] {inst['repo']} — {inst.get('problem_statement', '')[:100]}...{suffix}")
        print(f"\n总共 {len(instances)} 题 (dry-run 模式，未执行)")
        return

    write_predictions(output_file, output)
    logs_dir.mkdir(parents=True, exist_ok=True)

    passed = 0
    failed = 0
    skipped = 0

    for i, inst in enumerate(instances):
        inst_id = inst["instance_id"]
        repo = inst["repo"]
        print(f"[{i + 1}/{len(instances)}] {inst_id} ({repo})")

        if resume and has_existing_prediction(output, inst_id):
            print("  跳过: output 中已有 prediction")
            skipped += 1
            continue

        repo_dir = prepare_repo(inst, repos_base, args.clone_timeout)
        if not repo_dir:
            print("  跳过: 仓库准备失败")
            skipped += 1
            continue

        prompt = build_prompt(inst)
        t0 = time.time()

        stash_restore(repo_dir)
        run_result = run_nanocode(
            prompt,
            repo_dir,
            inst_id,
            logs_dir,
            api_config=api_config,
            max_turns=args.max_turns,
            max_cost=args.max_cost,
            timeout=args.timeout,
            live_output=not args.quiet,
        )
        diff = get_diff(repo_dir)
        stash_restore(repo_dir)

        elapsed = time.time() - t0

        if diff.strip():
            output[inst_id] = {
                "instance_id": inst_id,
                "model_patch": diff,
                "model_name_or_path": f"nanocode-{api_config.model}",
            }
            write_predictions(output_file, output)
            print(f"  ✅ 生成 patch ({len(diff)} 字符, {elapsed:.0f}s)")
            print(f"  日志: {run_result.log_file}")
            passed += 1
        else:
            print(f"  ❌ 无 diff ({elapsed:.0f}s, stdout: {len(run_result.stdout)} 字符)")
            print(f"  日志: {run_result.log_file}")
            failed += 1

    print(f"\n完成: {passed} passed, {failed} failed, {skipped} skipped")
    print(f"预测文件: {output_file}")
    print(f"日志目录: {logs_dir}")
    if passed > 0:
        print("\n评测命令:")
        print("  python -m swebench.harness.run_evaluation \\")
        print("    --dataset_name princeton-nlp/SWE-bench_Lite \\")
        print(f"    --predictions_path {output_file} \\")
        print(f"    --run_id nanocode_{api_config.model.replace('-', '_')}")


if __name__ == "__main__":
    main()
