"""Unit tests for the automation-builder system prompt.

The host API (signatures/returns) and the `ctx` field set are *derived* from their
real sources (render_host_api / context_globals), so the prompt can't drift from what
the sandbox actually provides.
"""
import pytest

from yaffo.background_tasks.automation_sandbox.executor import context_globals
from yaffo.site_agents.prompt_generator.automation_system_prompt import (
    _context,
    build_automation_builder_system_prompt,
)

pytestmark = pytest.mark.unit


def test_context_lists_every_ctx_field_from_the_real_shape():
    block = _context()
    # Every key the sandbox actually injects is documented (no drift, e.g. 'groups').
    for key in context_globals(None):
        assert f"ctx['{key}']" in block


def test_context_documents_groups():
    # Regression: 'groups' used to be missing from the hand-written list.
    assert "ctx['groups']" in _context()


def test_full_prompt_includes_required_sections():
    prompt = build_automation_builder_system_prompt()
    for tag in ("<context>", "<host_api>", "<batching>", "<progress>", "<events>"):
        assert tag in prompt
