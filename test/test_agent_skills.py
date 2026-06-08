from __future__ import annotations

import unittest

from nanocode.runtime.agent import Agent
from nanocode.domains.skills import SkillDefinition, SkillInvocationResult


class AgentSkillIntegrationTests(unittest.TestCase):
    def test_filter_skill_tools_applies_allowed_and_disallowed_lists(self) -> None:
        agent = Agent(api_key="test-key")
        skill = SkillDefinition(name="review", description="review")
        invocation = SkillInvocationResult(
            skill=skill,
            allowed_tools=["read_file", "grep_search", "run_shell"],
            disallowed_tools=["run_shell"],
        )
        tools = [
            {"name": "read_file"},
            {"name": "grep_search"},
            {"name": "run_shell"},
            {"name": "agent"},
            {"name": "write_file"},
        ]

        filtered = agent._filter_skill_tools(tools, invocation)

        self.assertEqual([t["name"] for t in filtered], ["read_file", "grep_search"])

    def test_reattach_active_skills_appends_to_latest_user_message(self) -> None:
        agent = Agent(api_key="test-key")
        skill = SkillDefinition(
            name="commit",
            description="commit",
            source="project",
            skill_dir="/tmp/commit",
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

        agent._reattach_active_skills()

        content = agent._anthropic_messages[-1]["content"]
        self.assertIn("current task", content)
        self.assertIn("[Active skill: commit]", content)
        self.assertIn("Commit skill body", content)

    def test_active_skill_disallowed_tools_are_hidden_from_model_schema(self) -> None:
        agent = Agent(api_key="test-key")
        skill = SkillDefinition(
            name="safe",
            description="safe",
            source="project",
            skill_dir="/tmp/safe",
        )
        invocation = SkillInvocationResult(
            skill=skill,
            rendered_prompt="Safe skill body",
            context="inline",
            disallowed_tools=["run_shell"],
        )
        agent._active_skills.record(invocation)

        names = [tool["name"] for tool in agent._current_tool_definitions()]

        self.assertNotIn("run_shell", names)
        self.assertIn("read_file", names)


if __name__ == "__main__":
    unittest.main()
