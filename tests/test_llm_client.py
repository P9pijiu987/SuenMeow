from bot.llm_client import LlmClient
from bot.settings import ModelRoute, ProviderConfig


def test_llm_client_unavailable_without_real_key() -> None:
    client = LlmClient(
        providers={
            "default": ProviderConfig(
                base_url="https://api.example.com/v1/chat/completions",
                api_key="replace_me",
                timeout_seconds=30,
            )
        },
        models={
            "planner": ModelRoute(provider="default", model="gpt-test"),
        },
    )
    assert client.is_route_available("planner") is False


def test_parse_json_object_returns_none_for_invalid_json() -> None:
    assert LlmClient.parse_json_object("not json") is None
    assert LlmClient.parse_json_object('{"ok": true}') == {"ok": True}
