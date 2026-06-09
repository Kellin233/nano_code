"""NanoCode agent adapter for Harbor/Terminal-Bench.

Implements the BaseAgent interface so NanoCode can be evaluated
on Terminal-Bench tasks.

Usage:
  harbor run --agent-import-path benchmarks/terminal-bench/agent.py:NanoCodeAgent \
             --model openai/gpt-5.5 \
             --dataset terminal-bench@2.0
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

logger = logging.getLogger(__name__)


class NanoCodeAgent(BaseAgent):
    """Terminal-Bench agent that delegates to NanoCode CLI."""

    SUPPORTS_ATIF = False
    SUPPORTS_WINDOWS = False

    def __init__(self, logs_dir: Path, model_name: str | None = None, **kwargs):
        super().__init__(logs_dir=logs_dir, model_name=model_name, **kwargs)
        self._agent_env = kwargs.get("agent_env", {})

    @staticmethod
    def name() -> str:
        return "nanocode"

    def version(self) -> str | None:
        try:
            from nanocode import __version__
            return __version__
        except ImportError:
            return "1.0.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        """在 Docker 容器中安装 NanoCode + 配置环境变量。"""
        self.logger.info("Setting up NanoCode agent...")

        # 安装 Python（不是所有任务容器都预装）
        await environment.exec(
            "apt-get update -qq && apt-get install -y -qq python3 python3-pip 2>&1 | tail -3"
        )
        # 安装依赖
        await environment.exec(
            "pip3 install --break-system-packages -q anthropic openai rich prompt_toolkit 2>&1 | tail -3"
        )
        # 安装 NanoCode
        repo_url = os.environ.get("NANOCODE_REPO_URL", "https://github.com/Kellin233/nano_code.git")
        await environment.exec(
            f"pip3 install --break-system-packages -q git+{repo_url} 2>&1 | tail -5"
        )
        self.logger.info("NanoCode installed")

        # 解析模型名（harbor 传 "openai/deepseek-chat" 或 "anthropic/claude-sonnet-4-6"）
        model_full = self.model_name or "openai/gpt-5.5"
        if "/" in model_full:
            provider, model_name = model_full.split("/", 1)
        else:
            provider, model_name = "anthropic", model_full

        # 按 provider 设置环境变量
        if provider == "openai":
            api_key = os.environ.get("OPENAI_API_KEY") or ""
            api_base = os.environ.get("OPENAI_BASE_URL") or ""
            env_setup = (
                f"export OPENAI_API_KEY='{api_key}'\n"
                f"export OPENAI_BASE_URL='{api_base}'\n"
                f"export NANO_CODE_MODEL='{model_name}'"
            )
        else:
            api_key = os.environ.get("ANTHROPIC_API_KEY") or ""
            api_base = os.environ.get("ANTHROPIC_BASE_URL") or ""
            env_setup = f"export ANTHROPIC_API_KEY='{api_key}'"
            if api_base:
                env_setup += f"\nexport ANTHROPIC_BASE_URL='{api_base}'"
            env_setup += f"\nexport NANO_CODE_MODEL='{model_name}'"

        await environment.exec(env_setup)
        self.logger.info(f"Setup complete ({provider}/{model_name})")

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        """在 Docker 容器中运行 NanoCode 完成任务。"""
        self.logger.info(f"Running: {instruction[:100]}...")

        # Shell 转义
        safe_instruction = instruction.replace("'", "'\"'\"'")

        # 解析模型名
        model_full = self.model_name or "openai/gpt-5.5"
        model_short = model_full.split("/", 1)[1] if "/" in model_full else model_full

        result = await environment.exec(
            f"nanocode --yolo --max-turns 30 --max-cost 5.00 --model {model_short} '{safe_instruction}'",
            timeout_sec=600,
        )

        stdout = result.stdout or ""
        stderr = result.stderr or ""
        output = stdout + "\n" + stderr
        self.logger.info(f"Output: {len(output)} chars, rc={result.return_code}")

        # 存入 context（AgentContext 使用 metadata dict 存自定义数据）
        context.metadata = {
            "output": output,
            "return_code": result.return_code,
        }

        # 尝试提取结构化结果
        try:
            json_match = re.search(r'\{[\s\S]*"result"[\s\S]*\}', output)
            if json_match:
                context.metadata["structured_output"] = json.loads(json_match.group(0))
        except Exception:
            pass
