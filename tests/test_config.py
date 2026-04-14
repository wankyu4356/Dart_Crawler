import pytest

from dart_crawler.config import Settings


def test_settings_requires_api_key(monkeypatch):
    monkeypatch.delenv("DART_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DART_API_KEY"):
        Settings.from_env()


def test_settings_loads_values(monkeypatch):
    monkeypatch.setenv("DART_API_KEY", "test-key")
    monkeypatch.setenv("REQUEST_TIMEOUT", "10")
    s = Settings.from_env()
    assert s.api_key == "test-key"
    assert s.request_timeout == 10
