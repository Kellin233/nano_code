import asyncio
import io
import unittest

from rich.console import Console

from nanocode.tui.app import TuiApp
from nanocode.tui.input import TuiInput
from nanocode.tui.renderer import TuiRenderer


class FakeAgent:
    def __init__(self):
        self.model = "test-model"
        self.is_processing = False
        self.confirm_fn = None
        self.aborted = False
        self.prompts = []

    def set_confirm_fn(self, fn):
        self.confirm_fn = fn

    async def chat(self, prompt):
        self.prompts.append(prompt)

    async def invoke_skill(self, skill_name, args="", invoked_by="user"):
        return f"{skill_name}:{args}:{invoked_by}"

    def abort(self):
        self.aborted = True


def make_app():
    output = io.StringIO()
    console = Console(file=output, force_terminal=False, no_color=True, highlight=False, width=120)
    renderer = TuiRenderer(console=console)
    input_ = TuiInput(force_simple=True, input_fn=lambda prompt="": "")
    agent = FakeAgent()
    return TuiApp(agent, input=input_, renderer=renderer), agent, output


def make_terminal_app(agent=None):
    output = io.StringIO()
    console = Console(file=output, force_terminal=True, no_color=True, highlight=False, width=120)
    renderer = TuiRenderer(console=console)
    input_ = TuiInput(force_simple=True, input_fn=lambda prompt="": "")
    input_._prompt_toolkit_ready = True
    agent = agent or FakeAgent()
    return TuiApp(agent, input=input_, renderer=renderer), agent, output


class TuiAppTest(unittest.TestCase):
    def test_prepare_sets_confirm_fn_and_completions(self):
        app, agent, _ = make_app()

        app._prepare()

        self.assertIsNotNone(agent.confirm_fn)
        self.assertIn("/help", app.input._completer.words)
        self.assertIn("/tokens", app.input._completer.words)
        self.assertEqual(app.input._completer.meta["/tokens"], "alias for /cost")

    def test_exit_command_stops_app(self):
        app, _, output = make_app()

        asyncio.run(app._handle_line("/exit"))

        self.assertTrue(app.state.should_exit)
        self.assertIn("Bye!", output.getvalue())

    def test_plain_input_is_sent_to_agent(self):
        app, agent, output = make_app()

        asyncio.run(app._handle_line("implement this"))

        self.assertEqual(agent.prompts, ["implement this"])
        text = output.getvalue()
        self.assertIn("working", text)
        self.assertIn("ready", text)
        self.assertNotIn("implement this", text)

    def test_fancy_input_renders_submitted_user_message_once(self):
        app, agent, output = make_app()
        app.input._prompt_toolkit_ready = True

        asyncio.run(app._handle_line("implement this"))

        self.assertEqual(agent.prompts, ["implement this"])
        self.assertEqual(output.getvalue().count("implement this"), 1)

    def test_fancy_terminal_chat_keeps_live_footer_while_streaming(self):
        class StreamingAgent(FakeAgent):
            def __init__(self):
                super().__init__()
                self.emit = None

            async def chat(self, prompt):
                self.prompts.append(prompt)
                self.emit()

        agent = StreamingAgent()
        app, agent, output = make_terminal_app(agent)
        agent.emit = lambda: app.renderer.assistant_delta("\nstream")

        asyncio.run(app._handle_line("implement this"))

        text = output.getvalue()
        self.assertIn("Working", text)
        self.assertIn("stream", text)
        self.assertIn("\x1b[J", text)
        self.assertIn("\x1b[s", text)
        self.assertIn("\x1b[u", text)

    def test_unhandled_slash_input_falls_back_to_chat(self):
        app, agent, _ = make_app()

        asyncio.run(app._handle_line("/unknown command"))

        self.assertEqual(agent.prompts, ["/unknown command"])

    def test_sigint_aborts_processing_agent(self):
        app, agent, output = make_app()
        agent.is_processing = True

        app._handle_sigint(None, None)

        self.assertTrue(agent.aborted)
        self.assertIn("interrupted", output.getvalue())

    def test_keyboard_interrupt_read_returns_none_without_goodbye(self):
        app, _, output = make_app()

        async def raise_interrupt(prompt):
            raise KeyboardInterrupt

        app.input.read = raise_interrupt

        result = asyncio.run(app._read_line())

        self.assertIsNone(result)
        self.assertNotIn("Bye!", output.getvalue())


if __name__ == "__main__":
    unittest.main()
