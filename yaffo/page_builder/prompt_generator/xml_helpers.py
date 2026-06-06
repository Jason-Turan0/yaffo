"""Small XML formatting helpers shared by the prompt builders.

The prompts are organized into XML-delimited sections (helps the model attend to
and recall each block). These helpers keep the formatting — escaping, attributes,
indentation, nesting — in one place.
"""
from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr


def _attrs(attributes: dict) -> str:
    return "".join(
        f" {key}={quoteattr(str(value))}"
        for key, value in attributes.items()
        if value not in (None, "")
    )


def open_tag(tag: str, **attributes) -> str:
    return f"<{tag}{_attrs(attributes)}>"


def el(tag: str, text: str = "", **attributes) -> str:
    """A single-line element with escaped text: <tag attr="...">text</tag>."""
    opened = open_tag(tag, **attributes)
    return f"{opened}</{tag}>" if text == "" else f"{opened}{escape(text)}</{tag}>"


def block(tag: str, children: list[str], **attributes) -> str:
    """A multi-line element wrapping child lines, each indented one level.

    Children may themselves be multi-line (nested blocks) — every line is
    indented, so nesting composes correctly.
    """
    inner = [f"    {line}" for child in children for line in child.split("\n")]
    return "\n".join([open_tag(tag, **attributes), *inner, f"</{tag}>"])