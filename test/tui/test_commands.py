import asyncio
import io
import unittest

from rich.console import Console

from nanocode.tui.commands import default_commands
from nanocode.tui.input import TuiInput
from nanocode.tui.renderer import TuiRenderer
from nanocode.tui.state import CommandContext, TuiState


class FakeAgent:
    def __init__(self):
        self.model = "test-model"
        self.cleared = False
        self.cost_shown = False
        self.compacted = False

    def clear_history(self):
        self.cleared = True

    def show_cost(self):
        self.cost_shown = True

    async def compact(self):
        self.compacted = True


def make_renderer():
    output = io.StringIO()
    console = Console(file=output, force_terminal=False, no_color=True, highlight=False, width=120)
    return TuiRenderer(console=console), output


def make_context():
    renderer, output = make_renderer()
    commands = default_commands()
    input_ = TuiInput(force_simple=True, input_fn=lambda prompt="": "")
    state = TuiState(
        agent=FakeAgent(),
        renderer=renderer,
        input=input_,
        commands=commands,
    )
    return commands, CommandContext(state), output


class CommandRegistryTest(unittest.TestCase):
    def test_help_lists_registered_commands(self):
        commands, ctx, output = make_context()

        result = asyncio.run(commands.dispatch("/help", ctx))

        self.assertTrue(result.handled)
        text = output.getvalue()
        self.assertIn("Commands:", text)
        self.assertIn("/help", text)
        self.assertIn("/multiline", text)

    def test_unknown_command_falls_through(self):
        commands, ctx, _ = make_context()

        result = asyncio.run(commands.dispatch("/does-not-exist", ctx))

        self.assertFalse(result.handled)

    def test_builtin_commands_call_agent_methods(self):
        commands, ctx, _ = make_context()

        asyncio.run(commands.dispatch("/clear", ctx))
        asyncio.run(commands.dispatch("/tokens", ctx))
        asyncio.run(commands.dispatch("/compact", ctx))

        self.assertTrue(ctx.agent.cleared)
        self.assertTrue(ctx.agent.cost_shown)
        self.assertTrue(ctx.agent.compacted)

    def test_multiline_toggles_input_mode(self):
        commands, ctx, output = make_context()

        asyncio.run(commands.dispatch("/multiline", ctx))

        self.assertTrue(ctx.input.multiline)
        self.assertTrue(ctx.state.multiline)
        self.assertIn("Multiline mode: on", output.getvalue())

    def test_exit_sets_state(self):
        commands, ctx, _ = make_context()

        result = asyncio.run(commands.dispatch("/quit", ctx))

        self.assertTrue(result.exit)
        self.assertTrue(ctx.state.should_exit)


if __name__ == "__main__":
    unittest.main()
