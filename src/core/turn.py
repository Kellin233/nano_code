"""Provider-neutral agent turn state machine."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal

from .messages import CoreToolCall, CoreToolResult, Message, ModelTextDelta, ModelTurnComplete
from .ports import ModelProvider, ToolExecutor


@dataclass(frozen=True)
class TurnToolCallStarted:
    call: CoreToolCall


@dataclass(frozen=True)
class TurnToolCallFinished:
    call: CoreToolCall
    result: CoreToolResult


@dataclass(frozen=True)
class TurnFinished:
    reason: Literal["stop", "aborted", "budget_exceeded", "error"]
    input_tokens: int = 0
    output_tokens: int = 0


TurnEvent = ModelTextDelta | TurnToolCallStarted | TurnToolCallFinished | TurnFinished


class AgentTurn:
    """Run a single user turn without knowing provider or tool internals."""

    def __init__(
        self,
        provider: ModelProvider,
        tools: ToolExecutor,
        *,
        max_tool_rounds: int | None = None,
    ):
        self.provider = provider
        self.tools = tools
        self.max_tool_rounds = max_tool_rounds

    async def run(self, messages: list[Message]) -> AsyncIterator[TurnEvent]:
        input_tokens = 0
        output_tokens = 0
        tool_rounds = 0

        while True:
            complete: ModelTurnComplete | None = None
            async for event in self.provider.stream_turn(messages):
                if isinstance(event, ModelTextDelta):
                    yield event
                elif isinstance(event, ModelTurnComplete):
                    complete = event
                else:
                    raise TypeError(f"unknown model event: {event!r}")

            if complete is None:
                yield TurnFinished("error", input_tokens, output_tokens)
                return

            input_tokens += complete.usage.input_tokens
            output_tokens += complete.usage.output_tokens
            messages.append(Message(role="assistant", content=complete.message.content))

            calls = complete.message.tool_calls
            if not calls:
                reason = "stop" if complete.stop_reason != "aborted" else "aborted"
                yield TurnFinished(reason, input_tokens, output_tokens)
                return

            tool_rounds += 1
            if self.max_tool_rounds is not None and tool_rounds > self.max_tool_rounds:
                yield TurnFinished("budget_exceeded", input_tokens, output_tokens)
                return

            for call in calls:
                yield TurnToolCallStarted(call)
            results = await self.tools.execute(calls)
            by_id = {result.call_id: result for result in results}
            for call in calls:
                result = by_id.get(call.id) or CoreToolResult(
                    call_id=call.id,
                    name=call.name,
                    content=f"Error: tool result missing for {call.name}",
                    is_error=True,
                )
                yield TurnToolCallFinished(call, result)
                messages.append(Message(
                    role="tool",
                    tool_call_id=call.id,
                    name=call.name,
                    content=result.content,
                ))
