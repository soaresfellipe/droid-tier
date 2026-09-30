import pytest

from droid_tier import quotas


@pytest.fixture(autouse=True)
def no_quota_calls(monkeypatch):
    """No test should reach a real provider; quota tests patch this again."""
    monkeypatch.setattr(quotas, "check", lambda cfg, settings: {})
