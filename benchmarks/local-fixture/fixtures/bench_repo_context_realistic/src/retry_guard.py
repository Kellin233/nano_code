class CheckoutRetryGuard:
    def __init__(self):
        self.seen_commit_tokens = set()

    def allow_commit(self, token: str) -> bool:
        if token in self.seen_commit_tokens:
            return False
        self.seen_commit_tokens.add(token)
        return True

def rollout_decision(duplicate_commits: int, guard_enabled: bool) -> str:
    if duplicate_commits > 0:
        return "rollback"
    if guard_enabled:
        return "ship-with-retry-guard"
    return "hold"
