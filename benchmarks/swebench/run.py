"""SWE-bench Lite 适配器。

用 NanoCode 一次性模式修复 GitHub issue，生成 SWE-bench 需要的 prediction JSON。

用法:
  # 跑 requests 和 flask（~6 题，$1，15 分钟）
  python benchmarks/swebench/run.py --repos psf/requests pallets/flask

  # 跑 requests + pytest（~20 题，$2，30 分钟）
  python benchmarks/swebench/run.py --repos psf/requests pytest-dev/pytest --limit 10

  # 跑全部 Lite（300 题，$30-50，几小时）
  python benchmarks/swebench/run.py

输出: benchmarks/swebench/predictions.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BENCH_DIR.parent.parent
OUTPUT_FILE = BENCH_DIR / "predictions.json"
LOG_DIR = BENCH_DIR / "logs"

# ─── 配置 ─────────────────────────────────────────

DEFAULT_MODEL = os.environ.get("NANO_CODE_MODEL", "claude-sonnet-4-6")
DEFAULT_MAX_TURNS = 20
DEFAULT_MAX_COST = 1.0
DEFAULT_TIMEOUT = 300  # 每题的秒数上限
API_KEY = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""


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
        "3. Edit ONLY the minimal code needed to fix it.\n"
        "4. Do NOT modify any test files.\n"
        "5. After editing, run the project's existing tests to verify your fix.\n"
        "6. Do NOT create new files unless absolutely necessary."
    )


def run_nanocode(prompt: str, cwd: Path, model: str, max_turns: int, max_cost: float, timeout: int) -> str:
    """用一次性模式跑 NanoCode，返回 stdout+stderr。"""
    env = os.environ.copy()
    env["NANO_CODE_MODEL"] = model
    if API_KEY:
        env["ANTHROPIC_API_KEY"] = API_KEY

    log_dir = LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"nanocode_{int(time.time())}.log"

    cmd = [
        "nanocode",
        "--yolo",
        "--max-turns", str(max_turns),
        "--max-cost", str(max_cost),
        "--model", model,
        prompt,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True,
            cwd=str(cwd),
            timeout=timeout,
            env=env,
        )
        log_file.write_text(
            f"=== STDOUT ===\n{result.stdout}\n=== STDERR ===\n{result.stderr}\n=== RC: {result.returncode}",
            encoding="utf-8",
        )
        return result.stdout
    except subprocess.TimeoutExpired:
        log_file.write_text(f"TIMEOUT after {timeout}s", encoding="utf-8")
        return ""
    except Exception as e:
        log_file.write_text(f"ERROR: {e}", encoding="utf-8")
        return ""


def get_diff(repo_dir: Path) -> str:
    """获取当前工作区的 unified diff。"""
    result = subprocess.run(
        ["git", "-C", str(repo_dir), "diff", "HEAD"],
        capture_output=True, text=True,
    )
    return result.stdout


def stash_restore(repo_dir: Path) -> None:
    """恢复 git 仓库到干净状态。"""
    subprocess.run(["git", "-C", str(repo_dir), "checkout", "."], capture_output=True)
    subprocess.run(["git", "-C", str(repo_dir), "clean", "-fd"], capture_output=True)


def load_instances(repos: list[str] | None = None, limit: int | None = None) -> list[dict]:
    """从 SWE-bench Lite 加载 instances，可选过滤 repo 和数量。"""
    try:
        from datasets import load_dataset
        ds = load_dataset("princeton-nlp/SWE-bench_Lite", split="test")
        instances = []
        for x in ds:
            if repos:
                if not any(r in x["repo"] for r in repos):
                    continue
            instances.append(x)
            if limit and len(instances) >= limit:
                break
        return instances
    except ImportError:
        print("需要安装 datasets: pip install datasets", file=sys.stderr)
        return []


def prepare_repo(instance: dict, repos_base: Path) -> Path | None:
    """clone 或准备 SWE-bench instance 对应的仓库。"""
    repo_name = instance["repo"]
    repo_dir = repos_base / repo_name.replace("/", "__")

    if not repo_dir.exists():
        print(f"  Cloning {repo_name}...")
        result = subprocess.run(
            ["git", "clone", f"https://github.com/{repo_name}.git", str(repo_dir)],
            capture_output=True, text=True,
            timeout=120,
        )
        if result.returncode != 0:
            print(f"  Clone failed: {result.stderr[:200]}")
            return None

    base_commit = instance.get("base_commit", "")
    if base_commit:
        subprocess.run(["git", "-C", str(repo_dir), "checkout", base_commit], capture_output=True)
        subprocess.run(["git", "-C", str(repo_dir), "clean", "-fd"], capture_output=True)

    return repo_dir


def main():
    parser = argparse.ArgumentParser(description="SWE-bench Lite adapter for NanoCode")
    parser.add_argument("--repos", nargs="*", default=None, help="只跑指定仓库 (如: psf/requests)")
    parser.add_argument("--limit", type=int, default=None, help="最多跑几个 instance")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"模型 (默认: {DEFAULT_MODEL})")
    parser.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    parser.add_argument("--max-cost", type=float, default=DEFAULT_MAX_COST)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--repos-base", default="/tmp/swebench-repos", help="仓库存储目录")
    parser.add_argument("--dry-run", action="store_true", help="只打印要跑的信息，不实际执行")
    args = parser.parse_args()

    if not API_KEY and not args.dry_run:
        print("需要设置 ANTHROPIC_API_KEY 或 OPENAI_API_KEY", file=sys.stderr)
        sys.exit(1)

    instances = load_instances(args.repos, args.limit)
    if not instances:
        print("没有找到匹配的 SWE-bench instances", file=sys.stderr)
        sys.exit(1)

    repos_base = Path(args.repos_base)
    repos_base.mkdir(parents=True, exist_ok=True)

    print(f"加载了 {len(instances)} 个 instances")
    print(f"模型: {args.model}, max_turns: {args.max_turns}, timeout: {args.timeout}s")
    print(f"仓库目录: {repos_base}")
    print()

    if args.dry_run:
        for inst in instances:
            print(f"  [{inst['instance_id']}] {inst['repo']} — {inst.get('problem_statement', '')[:100]}...")
        print(f"\n总共 {len(instances)} 题 (dry-run 模式，未执行)")
        return

    predictions = {}
    passed = 0
    failed = 0
    skipped = 0

    for i, inst in enumerate(instances):
        inst_id = inst["instance_id"]
        repo = inst["repo"]
        print(f"[{i + 1}/{len(instances)}] {inst_id} ({repo})")

        repo_dir = prepare_repo(inst, repos_base)
        if not repo_dir:
            print(f"  跳过: 仓库准备失败")
            skipped += 1
            continue

        prompt = build_prompt(inst)
        t0 = time.time()

        stash_restore(repo_dir)
        stdout = run_nanocode(
            prompt, repo_dir,
            model=args.model, max_turns=args.max_turns,
            max_cost=args.max_cost, timeout=args.timeout,
        )
        diff = get_diff(repo_dir)
        stash_restore(repo_dir)

        elapsed = time.time() - t0

        if diff.strip():
            predictions[inst_id] = {
                "instance_id": inst_id,
                "model_patch": diff,
                "model_name_or_path": f"nanocode-{args.model}",
            }
            print(f"  ✅ 生成 patch ({len(diff)} 字符, {elapsed:.0f}s)")
            passed += 1
        else:
            print(f"  ❌ 无 diff ({elapsed:.0f}s, stdout: {len(stdout)} 字符)")
            failed += 1

    output = {}
    try:
        with open(OUTPUT_FILE) as f:
            output = json.load(f)
    except Exception:
        pass
    output.update(predictions)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n完成: {passed} passed, {failed} failed, {skipped} skipped")
    print(f"预测文件: {OUTPUT_FILE}")
    if passed > 0:
        print(f"\n评测命令:")
        print(f"  python -m swebench.harness.run_evaluation \\")
        print(f"    --dataset_name princeton-nlp/SWE-bench_Lite \\")
        print(f"    --predictions_path {OUTPUT_FILE} \\")
        print(f"    --run_id nanocode_{args.model.replace('-', '_')}")


if __name__ == "__main__":
    main()
