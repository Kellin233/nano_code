"""测试子 Agent fork 和权限继承。

适配重构后 Agent(RuntimeConfig(...)) 的构造函数。
"""

from __future__ import annotations

import unittest

from nanocode.runtime.agent import Agent, RuntimeConfig
from nanocode.capabilities.sandbox.manager import SandboxManager
from nanocode.capabilities.sandbox.types import SandboxConfig


class TestSubAgentPermissionInheritance(unittest.TestCase):
    """子 Agent 权限继承测试。"""

    def _make_sub_agent(self, parent_permission_mode: str) -> Agent:
        config = RuntimeConfig(
            model="claude-sonnet-4-6",
            permission_mode=parent_permission_mode,
            custom_system_prompt="You are a sub-agent.",
            is_sub_agent=True,
            api_key="test-key",
        )
        return Agent(config, custom_tools=[
            {"name": "read_file", "description": "Read a file",
             "input_schema": {"type": "object", "properties": {"file_path": {"type": "string"}},
                              "required": ["file_path"]}},
            {"name": "run_shell", "description": "Run a shell command",
             "input_schema": {"type": "object", "properties": {"command": {"type": "string"}},
                              "required": ["command"]}},
        ])

    def test_sub_agent_inherits_parent_default_mode(self):
        sub = self._make_sub_agent("default")
        self.assertEqual(sub.permission_mode, "default")

    def test_sub_agent_inherits_parent_accept_edits_mode(self):
        sub = self._make_sub_agent("acceptEdits")
        self.assertEqual(sub.permission_mode, "acceptEdits")

    def test_sub_agent_inherits_parent_bypass_mode(self):
        sub = self._make_sub_agent("bypassPermissions")
        self.assertEqual(sub.permission_mode, "bypassPermissions")

    def test_sub_agent_does_not_force_bypass(self):
        for mode in ("default", "acceptEdits", "dontAsk"):
            sub = self._make_sub_agent(mode)
            self.assertEqual(sub.permission_mode, mode,
                             f"Sub-agent with parent mode '{mode}' should inherit '{mode}'")

    def test_sub_agent_sandbox_manager_shared_with_parent(self):
        parent_config = RuntimeConfig(
            model="claude-sonnet-4-6",
            permission_mode="default",
            is_sub_agent=False,
            api_key="test-key",
        )
        parent = Agent(parent_config)
        sub_config = RuntimeConfig(
            model="claude-sonnet-4-6",
            permission_mode=parent.permission_mode,
            custom_system_prompt="sub",
            is_sub_agent=True,
            api_key="test-key",
        )
        sub = Agent(sub_config, custom_tools=[], sandbox_manager=parent._sandbox_manager)
        self.assertIs(sub._sandbox_manager, parent._sandbox_manager)


if __name__ == "__main__":
    unittest.main()
