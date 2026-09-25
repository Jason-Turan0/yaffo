"""load_history seeds earlier plain-text turns in each provider's message shape."""
import pytest

from yaffo.site_agents.model_clients.model_client import AnthropicModelClient
from yaffo.site_agents.model_clients.openai_client import OpenAICompatibleModelClient

pytestmark = pytest.mark.unit

TURNS = [("user", "How do I add folders?"), ("assistant", "Open Settings.")]


def test_anthropic_history_uses_text_blocks():
    client = AnthropicModelClient(model="claude-haiku-4-5-20251001", system_prompt="s", api_key="k")
    client.load_history(TURNS)
    client.add_user_message("And then?")
    assert client.messages == [
        {"role": "user", "content": [{"type": "text", "text": "How do I add folders?"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Open Settings."}]},
        {"role": "user", "content": [{"type": "text", "text": "And then?"}]},
    ]


def test_openai_history_uses_plain_strings():
    client = OpenAICompatibleModelClient(
        model="gpt-5.1", provider_id="openai", base_url="https://api.openai.com/v1",
        system_prompt="s", api_key="k",
    )
    client.load_history(TURNS)
    assert client.messages == [
        {"role": "user", "content": "How do I add folders?"},
        {"role": "assistant", "content": "Open Settings."},
    ]
