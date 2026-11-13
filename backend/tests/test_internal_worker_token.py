"""Every worker call carries the shared secret, and the Anthropic provider builds."""

import httpx
import pytest
from langchain_anthropic import ChatAnthropic

from app.core.config import Settings
from app.core.llm import provider
from app.services import document_worker


def _settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "worker_url": "http://worker:8001",
        "internal_worker_secret": "s3cret-value",
    }
    values.update(overrides)
    return Settings(**values)


class CapturingClient:
    calls: list[tuple[str, str, dict]] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def request(self, method, url, **kwargs):
        CapturingClient.calls.append((method, url, kwargs))
        return httpx.Response(200, json={"status": "ok"}, request=httpx.Request(method, url))


@pytest.fixture
def captured(monkeypatch):
    CapturingClient.calls = []
    monkeypatch.setattr(document_worker.httpx, "AsyncClient", CapturingClient)
    return CapturingClient.calls


async def test_every_worker_call_carries_the_internal_token(monkeypatch, captured):
    monkeypatch.setattr(document_worker, "get_settings", lambda: _settings())

    await document_worker.worker_request("DELETE", "/pages/abc", params={"client_id": "x"})

    method, url, kwargs = captured[0]
    assert (method, url) == ("DELETE", "http://worker:8001/pages/abc")
    assert kwargs["headers"] == {"X-Internal-Token": "s3cret-value"}
    assert kwargs["params"] == {"client_id": "x"}


async def test_the_token_is_added_next_to_caller_headers(monkeypatch, captured):
    monkeypatch.setattr(document_worker, "get_settings", lambda: _settings())

    await document_worker.worker_request("PUT", "/faqs/abc", headers={"X-Trace": "1"}, json={})

    assert captured[0][2]["headers"] == {"X-Trace": "1", "X-Internal-Token": "s3cret-value"}


async def test_an_unset_secret_is_sent_empty_so_the_worker_refuses(monkeypatch, captured):
    monkeypatch.setattr(
        document_worker, "get_settings", lambda: _settings(internal_worker_secret="")
    )

    await document_worker.worker_request("GET", "/documents/abc/text")

    assert captured[0][2]["headers"] == {"X-Internal-Token": ""}


def test_anthropic_is_an_accepted_provider_and_builds_a_claude_model():
    model = provider.get_chat_model(
        settings=_settings(
            llm_provider="anthropic",
            anthropic_api_key="sk-ant-test-key",
            anthropic_model="claude-sonnet-5-5",
        )
    )
    assert isinstance(model, ChatAnthropic)
    assert model.model == "claude-sonnet-5-5"


def test_anthropic_provider_refuses_to_start_without_a_key():
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        provider.get_chat_model(
            settings=_settings(llm_provider="anthropic", anthropic_api_key="")
        )
