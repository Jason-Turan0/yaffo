"""Registry resolution + tool-schema conversion for the multi-vendor model clients."""
import pytest

from yaffo.site_agents.model_clients import providers
from yaffo.site_agents.model_clients.factory import create_model_client
from yaffo.site_agents.model_clients import AnthropicModelClient, OpenAICompatibleModelClient
from yaffo.site_agents.tool_providers.tool_provider_types import RawToolDefinition

pytestmark = pytest.mark.unit


def test_provider_for_model_resolves_known_ids():
    assert providers.provider_for_model("claude-opus-4-8").id == "anthropic"
    assert providers.provider_for_model("gpt-5.1").id == "openai"
    assert providers.provider_for_model("deepseek-chat").id == "deepseek"


def test_provider_for_unknown_model_is_none():
    assert providers.provider_for_model("nope") is None
    assert providers.pricing_for("nope") is None


def test_every_model_has_a_registered_provider():
    ids = {p.id for p in providers.PROVIDERS}
    for m in providers.MODELS:
        assert m.provider_id in ids, m.id


def test_anthropic_is_the_only_native_provider():
    # base_url=None marks the native-SDK path; everything else is OpenAI-compatible.
    native = [p.id for p in providers.PROVIDERS if p.base_url is None]
    assert native == ["anthropic"]


def test_raw_tool_to_openai_shape():
    schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    tool = RawToolDefinition(name="do_thing", description="desc", input_schema=schema)
    assert tool.to_openai_tool() == {
        "type": "function",
        "function": {"name": "do_thing", "description": "desc", "parameters": schema},
    }


def test_factory_picks_client_by_provider():
    anthropic = create_model_client(
        model="claude-opus-4-8", system_prompt="s", providers=[], api_key="k")
    openai = create_model_client(
        model="deepseek-chat", system_prompt="s", providers=[], api_key="k")
    assert isinstance(anthropic, AnthropicModelClient)
    assert isinstance(openai, OpenAICompatibleModelClient)
    assert openai.provider_id == "deepseek"


def test_factory_rejects_unknown_model():
    with pytest.raises(ValueError):
        create_model_client(model="nope", system_prompt="s", providers=[], api_key="k")
