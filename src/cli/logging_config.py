"""nanocode 全局日志配置。

所有模块使用 `logging.getLogger("nanocode.xxx")` 获取 logger。
CLI 入口在启动时调用 `setup_logging()` 配置输出级别和格式。
"""

from __future__ import annotations

import logging
import sys


def setup_logging(level: int = logging.INFO) -> None:
    """初始化 nanocode 全局日志，输出到 stderr（与 TUI stdout 分离）。"""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    root = logging.getLogger("nanocode")
    root.setLevel(level)
    if not root.handlers:  # 防止重复添加
        root.addHandler(handler)
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    """获取 nanocode 子模块 logger。"""
    return logging.getLogger(f"nanocode.{name}")
