class RetryGuard:
    def __init__(self):
        self.tokens = set()

    def should_commit(self, token: str) -> bool:
        if token in self.tokens:
            return False
        self.tokens.add(token)
        return True
