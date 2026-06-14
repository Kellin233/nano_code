from checkout.retry_guard import RetryGuard

def test_retry_guard_blocks_duplicate_commit():
    guard = RetryGuard()
    assert guard.should_commit("checkout-42") is True
    assert guard.should_commit("checkout-42") is False
