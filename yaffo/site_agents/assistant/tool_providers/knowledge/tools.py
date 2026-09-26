"""The assistant's docs tools: search_docs and read_doc over the bundled knowledge.

Each call returns a ToolResult: plain text for the model, and a ToolActivity for
the chat UI (the activity line and the sources linked under the answer).
"""
from __future__ import annotations

from typing import Optional

from yaffo.site_agents.assistant.tool_providers.knowledge.knowledge import SCOPES, DocSection, KnowledgeBase, knowledge_base
from yaffo.site_agents.assistant.schemas import DocSource, ToolActivity
from yaffo.site_agents.common.tool_providers.tool_provider_types import (
    CallToolReturn,
    RawToolDefinition,
    ToolProvider,
    ToolResult,
)
from yaffo.site_agents.common.tool_providers.utils import truncate_tool_result

SEARCH_DOCS = "search_docs"
READ_DOC = "read_doc"

_SEARCH_LIMIT = 6
_READ_MAX_CHARS = 12000

_SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Keywords describing what to look up, e.g. 'assign faces to a person'.",
        },
        "scope": {
            "type": "string",
            "enum": list(SCOPES),
            "description": "Limit to the user guide or the development notes. Omit to search both.",
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}

_READ_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "The page path from a search result, e.g. 'guide/organize-review/assigning-faces.md'.",
        },
        "anchor": {
            "type": "string",
            "description": "A section anchor from a search result. Omit to read the whole page.",
        },
    },
    "required": ["path"],
    "additionalProperties": False,
}


def _source(section: DocSection) -> DocSource:
    return DocSource(
        title=section.page_title, heading=section.heading, url=section.url, scope=section.scope)


def _location(section: DocSection) -> str:
    return f"{section.path}#{section.anchor}" if section.anchor else section.path


class KnowledgeToolProvider(ToolProvider):
    def __init__(self, knowledge: Optional[KnowledgeBase] = None):
        self._knowledge = knowledge

    @property
    def knowledge(self) -> KnowledgeBase:
        return self._knowledge if self._knowledge is not None else knowledge_base()

    def get_tools(self) -> list[RawToolDefinition]:
        return [
            RawToolDefinition(
                SEARCH_DOCS,
                "Search Yaffo's user guide and development notes. Returns the best-matching "
                "sections with a snippet, the page path, and the section anchor.",
                _SEARCH_SCHEMA,
            ),
            RawToolDefinition(
                READ_DOC,
                "Read a documentation page, or one section of it, by the path (and anchor) "
                "from a search result.",
                _READ_SCHEMA,
            ),
        ]

    def call_tool(self, name: str, args: dict) -> CallToolReturn:
        if name == SEARCH_DOCS:
            return self._search(str(args.get("query") or ""), args.get("scope"))
        if name == READ_DOC:
            return self._read(str(args.get("path") or ""), args.get("anchor"))
        raise ValueError(f"Unknown tool: {name}")

    def _search(self, query: str, scope: Optional[str]) -> ToolResult:
        if scope not in SCOPES:
            scope = None
        hits = self.knowledge.search(query, scope=scope, limit=_SEARCH_LIMIT)
        if not hits:
            text = f"No documentation matched {query!r}. Try different keywords."
        else:
            lines = []
            for number, hit in enumerate(hits, start=1):
                section = hit.section
                lines.append(
                    f"[{number}] {section.page_title} › {section.heading} "
                    f"({section.scope}; path: {section.path}; anchor: {section.anchor or '(page)'})"
                )
                lines.append(f"    {hit.snippet}")
            text = "\n".join(lines)
        activity = ToolActivity(
            tool=SEARCH_DOCS, query=query, count=len(hits),
            sources=[_source(hit.section) for hit in hits],
        )
        return ToolResult(model_text=text, host_data=activity.to_dict())

    def _read(self, path: str, anchor: Optional[str]) -> ToolResult:
        if anchor:
            section = self.knowledge.section(path, anchor)
            sections = [section] if section is not None else []
        else:
            sections = self.knowledge.page(path)
        if not sections:
            target = f"{path}#{anchor}" if anchor else path
            activity = ToolActivity(tool=READ_DOC, title=target, error=True)
            return ToolResult(
                model_text=f"No page or section found at {target!r}. Use a path and anchor from search_docs.",
                host_data=activity.to_dict(),
            )
        parts = [f"{sections[0].page_title} ({sections[0].scope}; path: {path})"]
        for section in sections:
            parts.append(f"\n## {section.heading} (anchor: {section.anchor or '(page)'})\n{section.text}")
        activity = ToolActivity(
            tool=READ_DOC,
            title=sections[0].page_title if not anchor else f"{sections[0].page_title} › {sections[0].heading}",
            sources=[_source(sections[0])],
        )
        return ToolResult(
            model_text=truncate_tool_result("\n".join(parts), _READ_MAX_CHARS),
            host_data=activity.to_dict(),
        )
