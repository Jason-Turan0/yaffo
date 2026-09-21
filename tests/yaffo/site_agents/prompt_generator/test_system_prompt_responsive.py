"""The widget system prompt has to *require* internally responsive widget HTML.

A widget's iframe is sized to the grid cell it occupies, and the page canvas
reflows from 12 columns to 6 to a single full-bleed column (see CANVAS_BANDS in
static/pages/grid.js). The host can hand the frame the right box, but only the
generated markup can make the content inside it read at that size — so the
requirement lives in the prompt, and these pin that it is still there.
"""
import re

import pytest

from yaffo.site_agents.prompt_generator.system_prompt import build_system_prompt
from yaffo.site_agents.widget_templates import TEMPLATES

pytestmark = pytest.mark.unit

# A width in the "a control, not an icon" range: wide enough to push a one-column
# widget frame sideways if it is not also capped to the frame.
_FIXED_WIDTH = re.compile(r"\bwidth:\s*(\d{3,})px")


class TestResponsiveRequirement:
    def test_prompt_declares_a_responsive_block(self):
        prompt = build_system_prompt()
        assert "<responsive>" in prompt and "</responsive>" in prompt

    def test_prompt_states_the_canvas_reflow(self):
        prompt = build_system_prompt()
        # The model has to know the widget is not rendered at one fixed size.
        assert "12 columns on a desktop" in prompt
        assert "1 full-bleed column on a phone" in prompt

    @pytest.mark.parametrize("requirement", [
        "Never set a fixed pixel width or height",
        "repeat(auto-fill, minmax(",
        "flex-wrap: wrap",
        "max-width: 100%",
        "min-width: 0",
        "44x44 CSS pixels",
        "logical properties",
    ])
    def test_prompt_carries_each_rule(self, requirement):
        assert requirement in build_system_prompt(), requirement


class TestCuratedTemplatesAreResponsive:
    """The curated templates are what the model is told to adapt first, so a
    template that cannot survive a one-column canvas teaches the wrong thing."""

    def test_no_template_pins_a_control_wider_than_its_frame(self):
        offenders = [
            f"{template.name}: {line.strip()}"
            for template in TEMPLATES
            for line in template.css.splitlines()
            if _FIXED_WIDTH.search(line)
            and "max-width: 100%" not in line
            and "min(" not in line
        ]
        assert offenders == [], offenders

    def test_toolbars_wrap(self):
        offenders = [
            f"{template.name}: {line.strip()}"
            for template in TEMPLATES
            for line in template.css.splitlines()
            if line.startswith((".bar {", ".fb-bar {")) and "flex-wrap: wrap" not in line
        ]
        assert offenders == [], offenders
