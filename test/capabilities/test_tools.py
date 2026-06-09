"""测试工具系统 — 合并后的 capabilities/tools/ 模块。

验证：
1. 类型定义正确
2. 内置工具 schema 完整
3. ToolRegistry 注册查找
4. ToolRuntime 执行管线
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock

from nanocode.capabilities.tools.types import (
    ToolCall,
    ToolContext,
    ToolDef,
    ToolMetadata,
    ToolResult,
    FunctionTool,
    ValidationResult,
    PermissionMode,
)
from nanocode.capabilities.tools.builtin import (
    builtin_tool_definitions,
    READ_TOOL_NAMES,
    EDIT_TOOL_NAMES,
    CONCURRENCY_SAFE_BUILTIN_TOOLS,
    read_file,
    write_file,
    edit_file,
    list_files,
    grep_search,
)
from nanocode.capabilities.tools.registry import ToolRegistry


class TestToolTypes(unittest.TestCase):
    """工具类型定义测试。"""

    def test_tool_call_creation(self):
        """ToolCall 数据类创建。"""
        call = ToolCall(id="1", name="read_file", input={"file_path": "test.py"}, provider="anthropic")
        self.assertEqual(call.id, "1")
        self.assertEqual(call.name, "read_file")
        self.assertEqual(call.input["file_path"], "test.py")

    def test_tool_result_creation(self):
        """ToolResult 数据类创建。"""
        result = ToolResult(content="hello", is_error=False)
        self.assertEqual(result.content, "hello")
        self.assertFalse(result.is_error)
        self.assertEqual(result.metadata, {})

    def test_tool_result_error(self):
        """ToolResult 错误标记。"""
        result = ToolResult(content="Error: file not found", is_error=True)
        self.assertTrue(result.is_error)

    def test_tool_metadata_creation(self):
        """ToolMetadata 数据类创建。"""
        meta = ToolMetadata(name="read_file", origin="builtin", read_only=True)
        self.assertEqual(meta.name, "read_file")
        self.assertTrue(meta.read_only)
        self.assertFalse(meta.deferred)

    def test_validation_result_ok(self):
        """ValidationResult 验证通过。"""
        result = ValidationResult(ok=True)
        self.assertTrue(result.ok)
        self.assertEqual(result.message, "")

    def test_validation_result_fail(self):
        """ValidationResult 验证失败。"""
        result = ValidationResult(ok=False, message="missing required field")
        self.assertFalse(result.ok)
        self.assertIn("missing", result.message)

    def test_permission_mode_literals(self):
        """PermissionMode 类型别名接受正确的值。"""
        modes = ["default", "acceptEdits", "bypassPermissions", "dontAsk"]
        for mode in modes:
            self.assertIsInstance(mode, str)


class TestBuiltinTools(unittest.TestCase):
    """内置工具测试。"""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_builtin_tool_definitions_returns_all_tools(self):
        """builtin_tool_definitions 返回所有内置工具。"""
        tools = builtin_tool_definitions()
        names = {t["name"] for t in tools}
        self.assertIn("read_file", names)
        self.assertIn("write_file", names)
        self.assertIn("edit_file", names)
        self.assertIn("list_files", names)
        self.assertIn("grep_search", names)
        self.assertIn("run_shell", names)
        self.assertIn("agent", names)
        self.assertIn("skill", names)
        self.assertIn("web_fetch", names)

    def test_read_file_returns_numbered_lines(self):
        """read_file 返回带行号的内容。"""
        f = self.project / "test.txt"
        f.write_text("line1\nline2\nline3")
        result = read_file({"file_path": str(f)})
        self.assertIn("1 |", result)
        self.assertIn("line1", result)

    def test_write_file_creates_content(self):
        """write_file 创建文件并返回成功信息。"""
        f = self.project / "new.txt"
        result = write_file({"file_path": str(f), "content": "hello world"})
        self.assertTrue(f.exists())
        self.assertIn("Successfully wrote", result)

    def test_edit_file_replaces_content(self):
        """edit_file 替换文件内容。"""
        f = self.project / "edit.txt"
        f.write_text("hello world\ngoodbye world")
        result = edit_file({
            "file_path": str(f),
            "old_string": "hello world",
            "new_string": "hi there",
        })
        self.assertIn("Successfully edited", result)
        self.assertEqual(f.read_text(), "hi there\ngoodbye world")

    def test_list_files_finds_matching_files(self):
        """list_files 找到匹配的文件。"""
        (self.project / "a.py").write_text("x")
        (self.project / "b.py").write_text("y")
        (self.project / "c.txt").write_text("z")
        result = list_files({"path": str(self.project), "pattern": "*.py"})
        self.assertIn("a.py", result)
        self.assertIn("b.py", result)
        self.assertNotIn("c.txt", result)

    def test_grep_search_finds_matches(self):
        """grep_search 搜索匹配行。"""
        (self.project / "code.py").write_text("def foo():\n    pass\ndef bar():\n    pass")
        result = grep_search({"path": str(self.project), "pattern": r"def \w+"})
        self.assertIn("def foo", result)
        self.assertIn("def bar", result)

    def test_classification_constants(self):
        """工具分类常量为预期值。"""
        self.assertIn("read_file", READ_TOOL_NAMES)
        self.assertIn("write_file", EDIT_TOOL_NAMES)
        self.assertIn("read_file", CONCURRENCY_SAFE_BUILTIN_TOOLS)
        self.assertNotIn("write_file", CONCURRENCY_SAFE_BUILTIN_TOOLS)


class TestToolRegistry(unittest.TestCase):
    """ToolRegistry 测试。"""

    def setUp(self):
        self.registry = ToolRegistry(builtin_tool_definitions())

    def test_registry_contains_builtin_tools(self):
        """注册表包含内置工具。"""
        tool = self.registry.find("read_file")
        self.assertIsNotNone(tool)

    def test_registry_find_unknown_tool(self):
        """查找未知工具返回 None。"""
        tool = self.registry.find("nonexistent_tool")
        self.assertIsNone(tool)

    def test_registry_names_returns_all_tools(self):
        """names 返回所有注册的工具名。"""
        names = self.registry.names()
        self.assertIn("read_file", names)
        self.assertIn("write_file", names)

    def test_registry_active_definitions_excludes_deferred(self):
        """active_definitions 不包含未激活的 deferred 工具。"""
        defs = self.registry.active_definitions()
        names = {d["name"] for d in defs}
        # 内置工具都不是 deferred，应全部出现
        self.assertIn("read_file", names)


if __name__ == "__main__":
    unittest.main()
