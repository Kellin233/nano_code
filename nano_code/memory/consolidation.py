"""Explicit maintenance for long-term memory files."""

from __future__ import annotations

from datetime import datetime, timezone

from .store import list_memories, update_importance, update_status
from .types import ConsolidationAction, ConsolidationResult, MemoryEntry

NEAR_DUPLICATE_THRESHOLD = 0.82
FEEDBACK_DUPLICATE_THRESHOLD = 0.90
DECAY_AFTER_DAYS = 45
DECAY_FACTOR = 0.05
MIN_IMPORTANCE = 0.15
ARCHIVE_AFTER_DAYS = 180
PINNED_IMPORTANCE = 0.95


def consolidate_memories(dry_run: bool = True) -> ConsolidationResult:
    entries = list_memories(include_inactive=False)
    result = ConsolidationResult(dry_run=dry_run)

    result.actions.extend(_exact_duplicate_actions(entries))
    result.actions.extend(_near_duplicate_actions(entries, {a.filename for a in result.actions}))
    result.actions.extend(_decay_actions(entries, {a.filename for a in result.actions}))
    result.actions.extend(_archive_actions(entries, {a.filename for a in result.actions}))

    if dry_run:
        return result

    for action in result.actions:
        if action.action == "supersede":
            update_status(action.filename, "superseded", superseded_by=action.target)
        elif action.action == "archive":
            update_status(action.filename, "archived")
        elif action.action == "decay" and action.new_importance is not None:
            update_importance(action.filename, action.new_importance)
    return result


def _exact_duplicate_actions(entries: list[MemoryEntry]) -> list[ConsolidationAction]:
    actions: list[ConsolidationAction] = []
    seen: dict[tuple[str, str], MemoryEntry] = {}
    for entry in entries:
        key = (entry.type, _normalize_content(entry.content))
        if not key[1]:
            continue
        if key not in seen:
            seen[key] = entry
            continue
        keep, drop = _choose_keep_drop(seen[key], entry)
        if drop.importance >= PINNED_IMPORTANCE:
            continue
        seen[key] = keep
        actions.append(ConsolidationAction(
            action="supersede",
            filename=drop.filename,
            target=keep.memory_id,
            reason=f"exact duplicate of {keep.filename}",
        ))
    return actions


def _near_duplicate_actions(
    entries: list[MemoryEntry],
    excluded: set[str],
) -> list[ConsolidationAction]:
    actions: list[ConsolidationAction] = []
    by_type: dict[str, list[MemoryEntry]] = {}
    for entry in entries:
        if entry.filename not in excluded:
            by_type.setdefault(entry.type, []).append(entry)

    for memory_type, group in by_type.items():
        threshold = FEEDBACK_DUPLICATE_THRESHOLD if memory_type == "feedback" else NEAR_DUPLICATE_THRESHOLD
        alive = set(entry.filename for entry in group)
        token_sets = {entry.filename: set(_tokenize(entry.content)) for entry in group}
        for i, left in enumerate(group):
            if left.filename not in alive or len(token_sets[left.filename]) < 8:
                continue
            for right in group[i + 1:]:
                if right.filename not in alive or len(token_sets[right.filename]) < 8:
                    continue
                similarity = _jaccard(token_sets[left.filename], token_sets[right.filename])
                if similarity < threshold:
                    continue
                keep, drop = _choose_keep_drop(left, right)
                if drop.importance >= PINNED_IMPORTANCE:
                    continue
                alive.discard(drop.filename)
                actions.append(ConsolidationAction(
                    action="supersede",
                    filename=drop.filename,
                    target=keep.memory_id,
                    reason=f"near duplicate of {keep.filename} (similarity={similarity:.2f})",
                ))
    return actions


def _decay_actions(entries: list[MemoryEntry], excluded: set[str]) -> list[ConsolidationAction]:
    actions: list[ConsolidationAction] = []
    now = datetime.now(timezone.utc)
    for entry in entries:
        if entry.filename in excluded or entry.importance <= MIN_IMPORTANCE or entry.importance >= PINNED_IMPORTANCE:
            continue
        age_days = _age_days(entry.last_accessed_at or entry.updated_at, now)
        if age_days < DECAY_AFTER_DAYS:
            continue
        periods = max(1.0, age_days / DECAY_AFTER_DAYS)
        new_importance = max(MIN_IMPORTANCE, entry.importance - DECAY_FACTOR * periods)
        if round(new_importance, 4) >= round(entry.importance, 4):
            continue
        actions.append(ConsolidationAction(
            action="decay",
            filename=entry.filename,
            reason=f"not accessed for {int(age_days)} days",
            new_importance=round(new_importance, 4),
        ))
    return actions


def _archive_actions(entries: list[MemoryEntry], excluded: set[str]) -> list[ConsolidationAction]:
    actions: list[ConsolidationAction] = []
    now = datetime.now(timezone.utc)
    for entry in entries:
        if entry.filename in excluded or entry.importance > MIN_IMPORTANCE or entry.importance >= PINNED_IMPORTANCE:
            continue
        age_days = _age_days(entry.last_accessed_at or entry.updated_at, now)
        if age_days < ARCHIVE_AFTER_DAYS:
            continue
        actions.append(ConsolidationAction(
            action="archive",
            filename=entry.filename,
            reason=f"low importance and inactive for {int(age_days)} days",
        ))
    return actions


def _choose_keep_drop(left: MemoryEntry, right: MemoryEntry) -> tuple[MemoryEntry, MemoryEntry]:
    if right.importance > left.importance:
        return right, left
    if right.importance == left.importance and right.updated_at > left.updated_at:
        return right, left
    return left, right


def _normalize_content(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _tokenize(text: str) -> list[str]:
    token: list[str] = []
    out: list[str] = []
    for ch in text.lower():
        if ch.isalnum() or ch in {"_", "-"}:
            token.append(ch)
            continue
        if token:
            item = "".join(token)
            if len(item) >= 2:
                out.append(item)
            token = []
    if token:
        item = "".join(token)
        if len(item) >= 2:
            out.append(item)
    return out


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / float(len(left | right))


def _age_days(value: str, now: datetime) -> float:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max((now - parsed).total_seconds() / 86400.0, 0.0)
