"""Runtime approval coordination."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

ApprovalStatus = Literal["approved", "denied"]
ConfirmFn = Callable[..., Awaitable[bool]]


@dataclass(frozen=True)
class ApprovalRequest:
    id: str
    message: str
    call_id: str | None = None
    tool_name: str | None = None
    requires_explicit_confirmation: bool = False


@dataclass(frozen=True)
class ApprovalDecision:
    request_id: str
    status: ApprovalStatus
    remember: bool = False

    @property
    def approved(self) -> bool:
        return self.status == "approved"


class ApprovalManager:
    def __init__(self):
        self._pending: dict[str, asyncio.Future[ApprovalDecision]] = {}
        self._remembered: set[str] = set()

    async def request(
        self,
        message: str,
        *,
        call_id: str | None = None,
        tool_name: str | None = None,
        requires_explicit_confirmation: bool = False,
        confirm_fn: ConfirmFn | None = None,
        on_request: Callable[[ApprovalRequest], None] | None = None,
    ) -> ApprovalDecision:
        if message in self._remembered:
            return ApprovalDecision(request_id="remembered", status="approved", remember=True)
        request = ApprovalRequest(
            id=uuid.uuid4().hex,
            message=message,
            call_id=call_id,
            tool_name=tool_name,
            requires_explicit_confirmation=requires_explicit_confirmation,
        )
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ApprovalDecision] = loop.create_future()
        self._pending[request.id] = future
        if on_request is not None:
            on_request(request)
        if confirm_fn is not None:
            try:
                approved = await confirm_fn(message)
                decision = ApprovalDecision(request.id, "approved" if approved else "denied")
                self.resolve(decision)
            except Exception:
                self.resolve(ApprovalDecision(request.id, "denied"))
        return await future

    def resolve(self, decision: ApprovalDecision) -> bool:
        future = self._pending.pop(decision.request_id, None)
        if future is None or future.done():
            return False
        if decision.approved and decision.remember:
            # The caller stores the message in its existing confirmed set; this
            # set covers protocol-driven approvals that request "don't ask".
            pass
        future.set_result(decision)
        return True

    def abort_pending(self) -> None:
        for request_id, future in list(self._pending.items()):
            if not future.done():
                future.set_result(ApprovalDecision(request_id, "denied"))
        self._pending.clear()
