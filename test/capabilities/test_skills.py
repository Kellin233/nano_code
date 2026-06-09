"""测试 Skill 系统 — 调用、激活状态、工具过滤。

适配重构后的模块结构（capabilities/skills/runtime.py）。
"""

from __future__ import annotations

import unittest

from nanocode.runtime.agent import Agent, RuntimeConfig
from nanocode.capabilities.skills import SkillDefinition, SkillInvocationResult, ActiveSkillManager, SkillInvocation


class TestSkillToolFiltering(unittest.TestCase):
    """Skill 工具过滤测试。"""

    def setUp(self):
        self.config = RuntimeConfig(api_key="test-key")
        self.agent = Agent(self.config)

    def test_active_skill_disallowed_tools_are_hidden(self):
        """Active skill 的 disallowed_tools 从 tool_definitions 中排除。"""
        skill = SkillDefinition(
            name="safe", description="safe", source="project", skill_dir="/tmp/safe"
        )
        invocation = SkillInvocationResult(
            skill=skill,
            rendered_prompt="Safe skill body",
            context="inline",
            disallowed_tools=["run_shell"],
        )
        self.agent._active_skills.record(invocation)

        names = [tool["name"] for tool in self.agent.tool_definitions()]
        self.assertNotIn("run_shell", names)
        self.assertIn("read_file", names)

    def test_active_skill_without_disallowed_does_not_filter(self):
        """无 disallowed_tools 的 skill 不影响工具列表。"""
        skill = SkillDefinition(
            name="helper", description="helper", source="project", skill_dir="/tmp/helper"
        )
        invocation = SkillInvocationResult(
            skill=skill,
            rendered_prompt="Helper skill body",
            context="inline",
        )
        self.agent._active_skills.record(invocation)

        names = [tool["name"] for tool in self.agent.tool_definitions()]
        self.assertIn("run_shell", names)


class TestActiveSkillReattach(unittest.TestCase):
    """Active skill compact 后重挂测试。"""

    def test_reattach_active_skills_appends_to_latest_user_message(self):
        """compact 后 active skill 上下文被追加到最新用户消息。"""
        config = RuntimeConfig(api_key="test-key")
        agent = Agent(config)

        skill = SkillDefinition(
            name="commit", description="commit", source="project", skill_dir="/tmp/commit",
        )
        invocation = SkillInvocationResult(
            skill=skill,
            args="use conventional commits",
            invoked_by="user",
            rendered_prompt="Commit skill body",
            context="inline",
        )
        agent._active_skills.record(invocation)
        agent._anthropic_messages = [{"role": "user", "content": "current task"}]

        # 通过 compressor 的 reattach 方法
        from nanocode.runtime.compressor import Compressor
        compressor = Compressor(agent)
        compressor._reattach_active_skills()

        content = agent._anthropic_messages[-1]["content"]
        self.assertIn("current task", content)
        self.assertIn("[Active skill: commit]", content)
        self.assertIn("Commit skill body", content)


class TestSkillInvocation(unittest.TestCase):
    """SkillInvocation 基础测试。"""

    def test_unknown_skill_returns_error(self):
        invoker = SkillInvocation()
        result = invoker.invoke("nonexistent_skill", invoked_by="user")
        self.assertFalse(result.ok)
        self.assertIn("Unknown skill", result.error)

    def test_user_invocable_skill(self):
        """用户可调用的 skill 正常通过。"""
        skill_def = SkillDefinition(
            name="test-skill",
            description="A test",
            source="project",
            skill_dir="/tmp/test-skill",
            prompt_template="Do this: $ARGUMENTS",
            user_invocable=True,
        )
        invoker = SkillInvocation()
        # 直接测试 render_prompt，绕过 registry 查找
        rendered = invoker.render_prompt(skill_def, "fix the bug")
        self.assertIn("fix the bug", rendered)
        self.assertIn("Do this", rendered)


class TestActiveSkillManager(unittest.TestCase):
    """ActiveSkillManager 测试。"""

    def test_disallowed_tools_aggregation(self):
        """多个 active skill 的 disallowed_tools 被汇总。"""
        mgr = ActiveSkillManager()
        skill1 = SkillDefinition(name="s1", description="s1", source="p", skill_dir="/tmp/s1")
        skill2 = SkillDefinition(name="s2", description="s2", source="p", skill_dir="/tmp/s2")

        mgr.record(SkillInvocationResult(
            skill=skill1, rendered_prompt="p1", context="inline", disallowed_tools=["run_shell"]
        ))
        mgr.record(SkillInvocationResult(
            skill=skill2, rendered_prompt="p2", context="inline", disallowed_tools=["write_file"]
        ))

        denied = mgr.disallowed_tools()
        self.assertIn("run_shell", denied)
        self.assertIn("write_file", denied)

    def test_build_context_when_empty(self):
        """无 active skill 时 build_context 返回空字符串。"""
        mgr = ActiveSkillManager()
        self.assertEqual(mgr.build_context(), "")

    def test_clear_removes_all(self):
        """clear 后无 active skill。"""
        mgr = ActiveSkillManager()
        skill = SkillDefinition(name="s1", description="s1", source="p", skill_dir="/tmp/s1")
        mgr.record(SkillInvocationResult(skill=skill, rendered_prompt="p1", context="inline"))
        mgr.clear()
        self.assertEqual(mgr.build_context(), "")


if __name__ == "__main__":
    unittest.main()
