"""测试上下文构建模块。

验证：
1. System prompt 构建
2. Startup context 构建
3. 附件渲染
4. Frontmatter 解析
"""

from __future__ import annotations

import unittest
from pathlib import Path

from nanocode.context.builder import (
    build_stable_system_prompt,
    build_system_prompt,
    build_startup_context,
    render_deferred_tools_attachment,
    render_system_reminder,
    render_mcp_delta_attachment,
)
from nanocode.context.sources import parse_frontmatter, format_frontmatter, collect_git_context


class TestSystemPrompt(unittest.TestCase):
    """System prompt 构建测试。"""

    def test_build_stable_system_prompt_is_non_empty(self):
        """稳定 system prompt 非空。"""
        prompt = build_stable_system_prompt()
        self.assertIsInstance(prompt, str)
        self.assertTrue(len(prompt) > 1000)

    def test_build_stable_system_prompt_contains_key_sections(self):
        """System prompt 包含关键章节。"""
        prompt = build_stable_system_prompt()
        self.assertIn("Nano Code", prompt)
        self.assertIn("Doing tasks", prompt)
        self.assertIn("Using your tools", prompt)

    def test_build_system_prompt_returns_same_as_stable(self):
        """build_system_prompt 和 build_stable_system_prompt 返回相同内容。"""
        self.assertEqual(build_system_prompt(), build_stable_system_prompt())

    def test_build_system_prompt_accepts_null_deferred(self):
        """build_system_prompt 接受 None 参数（向后兼容）。"""
        prompt = build_system_prompt(None)
        self.assertIsInstance(prompt, str)


class TestStartupContext(unittest.TestCase):
    """Startup context 构建测试。"""

    def test_build_startup_context_contains_date(self):
        """Startup context 包含当前日期。"""
        context = build_startup_context(cwd=Path.cwd())
        self.assertIn("Current date", context)
        self.assertIn("system-reminder", context)

    def test_build_startup_context_contains_platform(self):
        """Startup context 包含平台信息。"""
        context = build_startup_context(cwd=Path.cwd())
        self.assertIn("Platform", context)
        self.assertIn("Shell", context)

    def test_build_startup_context_with_git(self):
        """Startup context 可包含 git 上下文。"""
        context = build_startup_context(
            cwd=Path.cwd(),
            git_context="Branch: main\nRecent commits:\nabc123 fix bug",
        )
        self.assertIn("Branch: main", context)

    def test_build_startup_context_with_project_instructions(self):
        """Startup context 可包含项目指令。"""
        context = build_startup_context(
            cwd=Path.cwd(),
            project_instructions="## Project Rules\nAlways use tabs",
        )
        self.assertIn("Project instructions", context)


class TestAttachmentRendering(unittest.TestCase):
    """附件渲染测试。"""

    def test_render_system_reminder_wraps_content(self):
        """render_system_reminder 用 system-reminder 标签包裹内容。"""
        result = render_system_reminder("Test", "Hello world")
        self.assertIn("system-reminder", result)
        self.assertIn("Test", result)
        self.assertIn("Hello world", result)

    def test_render_system_reminder_empty_body(self):
        """空 body 返回空字符串。"""
        result = render_system_reminder("Test", "")
        self.assertEqual(result, "")

    def test_render_deferred_tools_empty(self):
        """空工具列表返回空字符串。"""
        result = render_deferred_tools_attachment([])
        self.assertEqual(result, "")

    def test_render_deferred_tools_lists_names(self):
        """render_deferred_tools_attachment 列出工具名。"""
        result = render_deferred_tools_attachment(["tool_a", "tool_b"])
        self.assertIn("tool_a", result)
        self.assertIn("tool_b", result)
        self.assertIn("tool_search", result)

    def test_render_mcp_delta_empty(self):
        """无变更的 MCP delta 返回空字符串。"""
        class EmptyDelta:
            added = []
            removed = []
            changed = []

        result = render_mcp_delta_attachment(EmptyDelta())
        self.assertEqual(result, "")

    def test_render_mcp_delta_shows_changes(self):
        """MCP delta 显示添加/删除/变更的工具。"""
        class Delta:
            added = ["tool_a"]
            removed = ["tool_b"]
            changed = ["tool_c"]

        result = render_mcp_delta_attachment(Delta())
        self.assertIn("tool_a", result)
        self.assertIn("tool_b", result)
        self.assertIn("tool_c", result)


class TestFrontmatter(unittest.TestCase):
    """Frontmatter 解析测试。"""

    def test_parse_frontmatter_extracts_metadata(self):
        """parse_frontmatter 正确提取 YAML 元数据。"""
        content = "---\nname: test\ndescription: A test\n---\n\nBody content"
        result = parse_frontmatter(content)
        self.assertEqual(result.meta["name"], "test")
        self.assertEqual(result.meta["description"], "A test")
        self.assertIn("Body content", result.body)

    def test_parse_frontmatter_no_frontmatter(self):
        """无 frontmatter 的内容返回空 meta。"""
        content = "Just plain text"
        result = parse_frontmatter(content)
        self.assertEqual(result.meta, {})
        self.assertEqual(result.body, content)

    def test_format_frontmatter_roundtrip(self):
        """format_frontmatter 可还原基本 frontmatter。"""
        meta = {"name": "test", "type": "user"}
        body = "Memory content"
        formatted = format_frontmatter(meta, body)
        parsed = parse_frontmatter(formatted)
        self.assertEqual(parsed.meta["name"], "test")
        self.assertEqual(parsed.meta["type"], "user")
        self.assertIn("Memory content", parsed.body)


if __name__ == "__main__":
    unittest.main()
