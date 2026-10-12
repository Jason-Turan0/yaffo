"""The per-request user turn for the service builder: the request, the service being
built, its current draft and the problems its check found, and the user's latest Test
(the one that reads live). Volatile, so it stays out of the cached system prompt."""
from __future__ import annotations

from yaffo.site_agents.common.prompt_generator.response_language import application_locale_el
from yaffo.site_agents.common.prompt_generator.xml_helpers import block, el


def build_adapter_user_message(request: str, *, name: str, locale: str = "en", current_code: str = "",
                               problems: list[str] | None = None, published: bool = False,
                               last_test: str = "") -> str:
    parts: list[str] = [
        block("request", (request.strip() or "").splitlines() or [""]),
        application_locale_el(locale),
        block("service", [
            el("name", name),
            el("published", "yes: keep its direction (role); syncs use it" if published else "not yet"),
            el("instruction", "Write the whole adapter module and pass it to write_adapter_code, then test_adapter."),
        ]),
    ]
    if current_code.strip():
        children = [el("instructions", "The current draft: revise it to honor the request; keep what still fits."),
                    block("code", current_code.splitlines())]
        if problems:
            children.append(block("check_problems", problems))
        parts.append(block("current_code", children))
    if last_test.strip():
        parts.append(block("user_test", [el("instructions", "The user's latest Test of the draft, with live reads."),
                                         *last_test.splitlines()]))
    return "\n\n".join(parts)
