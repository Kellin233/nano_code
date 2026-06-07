from __future__ import annotations

import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import nano_code.session as session_mod
from nano_code.agent import Agent
from nano_code.agent.context import MICROCOMPACT_IDLE_S, SNIP_PLACEHOLDER
from nano_code.frontmatter import parse_frontmatter
from nano_code.memory.retrieval import select_relevant_memories
from nano_code.memory.store import get_memory_dir, list_memories, save_memory
from nano_code.context.attachments import render_deferred_tools_attachment
from nano_code.prompt import build_prompt_bundle, build_system_prompt
from nano_code.skill import SkillInvocation, SkillRegistry
from nano_code.skill.registry import reset_skill_cache
from nano_code.subagent import get_sub_agent_config, reset_agent_cache


class MemorySkillSessionContextV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.project = self.root / "project"
        self.home.mkdir()
        self.project.mkdir()
        self.old_cwd = os.getcwd()
        os.chdir(self.project)
        self.home_patch = patch("pathlib.Path.home", return_value=self.home)
        self.home_patch.start()
        self.old_session_dir = session_mod.SESSION_DIR
        session_mod.SESSION_DIR = self.home / ".nano-code" / "sessions"
        reset_skill_cache()
        reset_agent_cache()

    def tearDown(self) -> None:
        reset_agent_cache()
        reset_skill_cache()
        session_mod.SESSION_DIR = self.old_session_dir
        self.home_patch.stop()
        os.chdir(self.old_cwd)
        self.tmp.cleanup()

    def test_memory_retrieval_falls_back_on_bad_side_query_and_respects_already_surfaced(self) -> None:
        filename = save_memory(
            "Deploy Notes",
            "deployment service checklist",
            "project",
            "deploy " * 900,
            keywords="deploy, service",
            importance=0.9,
        )

        async def bad_side_query(system: str, user: str) -> str:
            return "not json"

        selected = asyncio.run(select_relevant_memories("deploy service", bad_side_query, set()))
        already = asyncio.run(select_relevant_memories("deploy service", bad_side_query, {filename}))

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].filename, filename)
        self.assertIn("truncated, memory file too large", selected[0].content)
        self.assertEqual(already, [])

    def test_skill_project_override_restrictions_and_placeholder_rendering(self) -> None:
        user_skill = self.home / ".claude" / "skills" / "deploy"
        project_skill = self.project / ".claude" / "skills" / "deploy"
        user_skill.mkdir(parents=True)
        project_skill.mkdir(parents=True)
        (user_skill / "SKILL.md").write_text("""---
name: deploy
description: user copy
---
user body
""")
        (project_skill / "SKILL.md").write_text("""---
name: deploy
description: project copy
context: fork
allowed-tools: ["read_file", "grep_search"]
user-invocable: false
---
Use $0 then $ARGUMENTS[1] from ${CLAUDE_SKILL_DIR}.
""")
        registry = SkillRegistry(user_dir=self.home / ".claude" / "skills", project_dir=self.project / ".claude" / "skills")
        invocation = SkillInvocation(registry)

        skill = registry.get("deploy")
        model_result = invocation.invoke("deploy", '"alpha beta" gamma', invoked_by="model")
        user_result = invocation.invoke("deploy", "x", invoked_by="user")

        self.assertEqual(skill.description, "project copy")
        self.assertEqual(skill.context, "fork")
        self.assertEqual(skill.allowed_tools, ["read_file", "grep_search"])
        self.assertTrue(model_result.ok)
        self.assertIn("alpha beta then gamma", model_result.rendered_prompt)
        self.assertIn(str(project_skill), model_result.rendered_prompt)
        self.assertFalse(user_result.ok)
        self.assertIn("not user-invocable", user_result.error)

    def test_session_ignores_corrupt_files_and_sorts_latest_by_start_time(self) -> None:
        session_mod.save_session("old", {"metadata": {"id": "old", "startTime": "2025-01-01T00:00:00Z"}})
        session_mod.save_session("new", {"metadata": {"id": "new", "startTime": "2026-01-01T00:00:00Z"}})
        session_mod.SESSION_DIR.mkdir(parents=True, exist_ok=True)
        (session_mod.SESSION_DIR / "bad.json").write_text("{bad")

        sessions = session_mod.list_sessions()

        self.assertEqual(session_mod.get_latest_session_id(), "new")
        self.assertIsNone(session_mod.load_session("bad"))
        self.assertEqual({s["id"] for s in sessions}, {"old", "new"})

    def test_frontmatter_prompt_and_subagent_custom_agent_edges(self) -> None:
        parsed = parse_frontmatter("---\nurl: http://example.test/a:b\n---\nbody")
        malformed = parse_frontmatter("---\nname: missing end\nbody")
        (self.project / "extra.md").write_text("included instructions")
        (self.project / "CLAUDE.md").write_text("base\n@./extra.md")
        custom_agent = self.project / ".claude" / "agents"
        custom_agent.mkdir(parents=True)
        (custom_agent / "reviewer.md").write_text("""---
name: reviewer
description: reviews only
allowed-tools: read_file, grep_search
---
Review without editing.
""")
        reset_agent_cache()

        prompt = build_system_prompt(deferred_tool_names=["rare_tool"])
        bundle = build_prompt_bundle()
        deferred = render_deferred_tools_attachment(["rare_tool"])
        config = get_sub_agent_config("reviewer")

        self.assertEqual(parsed.meta["url"], "http://example.test/a:b")
        self.assertEqual(malformed.body, "---\nname: missing end\nbody")
        self.assertNotIn("included instructions", prompt)
        self.assertNotIn("rare_tool", prompt)
        self.assertIn("included instructions", bundle.startup_context)
        self.assertIn("rare_tool", deferred)
        self.assertEqual({tool["name"] for tool in config["tools"]}, {"read_file", "grep_search"})
        self.assertIn("Review without editing", config["system_prompt"])

    def test_context_pipeline_snips_old_anthropic_tool_results_without_breaking_recent_results(self) -> None:
        agent = Agent(api_key="test-key", is_sub_agent=True)
        agent.effective_window = 100
        agent.last_input_token_count = 80
        agent.last_api_call_time = time.time() - MICROCOMPACT_IDLE_S - 1
        for idx in range(5):
            agent._anthropic_messages.append({
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "id": f"tool-{idx}",
                    "name": "read_file",
                    "input": {"file_path": "same.txt"},
                }],
            })
            agent._anthropic_messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": f"tool-{idx}",
                    "content": f"result {idx}",
                }],
            })

        agent._run_compression_pipeline()
        results = [
            msg["content"][0]["content"]
            for msg in agent._anthropic_messages
            if msg.get("role") == "user"
        ]

        self.assertEqual(results[:-1], [SNIP_PLACEHOLDER] * 4)
        self.assertEqual(results[-1], "result 4")
        self.assertTrue(get_memory_dir().exists())


if __name__ == "__main__":
    unittest.main()
