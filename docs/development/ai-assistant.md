# AI Assistant

Ask Yaffo is the in-app assistant for questions about the product, the local
library, and problems with this installation. It can propose library changes,
but a user must review and approve a change plan before anything is applied.
The [user guide](../guide/start-here/ask-yaffo.md) describes the user-facing
workflow. This page describes the implementation boundaries and the places to
change them.

## Architecture

The assistant uses the shared model clients and agent loop in `yaffo/site_agents/`.
It uses the model and API key selected under **Settings → AI Generation**. The
assistant is disabled in demo mode. Its entry points appear when the assistant
is enabled and the selected provider has a key.

| Part | Responsibility |
|---|---|
| `yaffo/routes/assistant.py` | Conversation, cancellation, plan, and settings endpoints |
| `yaffo/background_tasks/tasks/assistant_run.py` | Background conversation turns |
| `yaffo/site_agents/assistant/` | Prompt, history, settings, tools, redaction, and plans |
| `yaffo/background_tasks/automation_sandbox/` | Starlark execution and the shared host function registry |
| `yaffo/db/repositories/assistant_repository.py` | Conversations, events, and change plans |
| `yaffo/static/assistant/` and `yaffo/templates/assistant/` | Panel, full page, activity lines, and plan cards |

The browser posts a message, then polls the conversation while an
`assistant_run` task executes at interactive queue priority. Events are stored
as they arrive, so closing the panel does not lose the conversation. The task
can be cancelled between agent iterations. A queued run can report why it is
waiting for a worker.

The panel is available throughout the app; the full page also lists saved
conversations. Error messages, failed jobs, and failed automation runs can open
it with a small, allowlisted context payload. When job or automation context is
attached and the relevant diagnostic group is enabled, the assistant fetches
the corresponding status before the model starts. These checks are visible as
activity in the conversation. Conversation events and model call logs remain
local until the user deletes the conversation.

## Knowledge and tools

The assistant has three kinds of capability:

| Kind | Use |
|---|---|
| Bundled documentation | `search_docs` and `read_doc` answer product questions without reading local library data. |
| Native diagnostic tools | Read bounded information about logs, library state, files and drives, background work, and one media item. Settings controls which groups are offered. |
| `run_script` | Runs Starlark over the assistant profile of the shared automation host API. Reads run against the library; enabled mutations are recorded as a plan. |

`describe_data_source` exposes query schemas. The prompt includes the available
data sources and enabled host functions, so it describes only capabilities the
model can use. Link tools build validated links to a filtered gallery or an app
page, and buttons for opening an indexed file on this computer. The model does
not construct arbitrary URLs or filesystem paths.

Diagnostic tools are separate from Starlark host functions. They cover health
checks, process and job status, recent errors, configured media folders,
filesystem probes, sharing status, and item-level reports. Results are capped,
redacted, and shown in expandable activity lines. A separate, default-off
setting permits reading capture-date metadata for an indexed item; it does not
read image pixels. `tool_providers/diagnostics/` is the source of truth for the
available tools and their limits.

`run_script` uses the automation sandbox's `assistant` profile. The interpreter
has no imports, shell, direct filesystem access, or network access. It can call
only bound host functions, and runs with time, host-call, output, and query
limits. Read functions run live. Mutating functions that are enabled in
**Settings → Assistant** record their arguments instead of changing the library.
A failed script records no plan. The script and its result are visible in the
conversation. `automation_host.py` defines the host API, profiles, risk and undo
metadata; `tool_providers/script_tool.py` binds the assistant's allowed subset.

### Knowledge bundle

`scripts/build_assistant_knowledge.py` turns `docs/index.md`, the user guide,
and development Markdown into searchable sections in
`yaffo/assistant_knowledge/sections.jsonl`. Its manifest records a source hash,
and a test detects stale output. The bundle is packaged with the app so
documentation search works offline. Search covers guide and development pages;
the prompt prefers guide pages for user questions.

After changing included documentation, rebuild the bundle from the repo root:

```bash
python -m scripts.build_assistant_knowledge
```

App-page links use the separately generated
`yaffo/assistant_knowledge/pages.json` catalog. After changing a page route or
its classification, run `python -m scripts.build_assistant_pages` and verify the
route tests. The worker uses this catalog because it does not have a Flask app.

## Change plans

When a script calls an enabled mutating host function, `plans.py` stores the
ordered calls and their arguments as a pending change plan. The server, rather
than the model's prose, supplies each card's summary, item count, risk, and undo
status. A script can combine related changes in one plan, including references
to results of earlier steps such as a newly created album.

Approval rechecks the plan's expiry, action switches, and preconditions. It
then replays the **recorded calls**, in order, through the live host functions;
it does not run the script again. The recorded arguments therefore define the
items to change even if the library has changed since the proposal. Declining
discards the proposal. Pending plans expire after 30 minutes.

The Settings switches control which changes can be proposed. High-risk actions
start disabled and require a typed item count when enabled. Plans above the
configured count threshold, 500 by default, require an additional confirmation.
File rename, move, and trash actions are high risk. The assistant cannot
permanently delete files, edit builder code, or change sharing configuration.
Recurring rules belong in Automations.

Reversible steps capture inverse calls immediately before execution. Undo runs
those calls in reverse order and leaves items alone if they changed again in the
meantime. Some actions have no inverse; their cards say so. A plan that fails
partway reports which steps ran and can undo completed reversible steps. Plan
outcomes are recorded in the conversation and are available to the model on a
later turn. `plans.py` owns validation, replay, and undo; the host function
registry owns action availability and risk.

## Local data and network boundaries

The assistant sends the conversation and selected tool results to the configured
model provider. Tool results can contain names, counts, settings, log excerpts,
and paths. Before sending them, Yaffo replaces configured folders with labels,
shortens other home paths, and masks likely credentials, email addresses, and
precise coordinates. The expanded activity line shows the redacted result the
model received. Previous tool results may be included as bounded historical
evidence on follow-up turns, subject to current diagnostic settings and
redaction. People's names are not redacted.

Native filesystem diagnostics accept named roots and relative paths, enforce
root confinement, reject sensitive files, and cap reads and listings. Slow
external-drive probes have timeouts. File-opening buttons resolve an indexed
media item or a path inside a configured media folder again when clicked; they
do not store an unrestricted path supplied by the model.

The assistant's only outbound connection is the selected model API. Its tools
do not browse the web, contact the sharing hub, or upload photos, videos, the
database, or diagnostics. The Starlark sandbox cannot make network calls. Code
that adds an assistant tool or host function must preserve these boundaries.

## Settings and persistence

**Settings → Assistant** controls the feature, diagnostic groups, individual
actions, and the count confirmation threshold. Logs, library, files, and jobs
diagnostics are enabled by default; capture-date metadata is off by default.
With all diagnostic groups off, the assistant can still answer from the bundled
docs. Low- and medium-risk actions are enabled by default; high-risk actions
must be turned on explicitly. The selected AI Generation model is shared with
the other builders; there is no assistant-specific model setting.

Conversations, transcript events, and change plans live in assistant-specific
database tables. The assistant does not use page-builder conversations. Model
call logs are stored by conversation and removed when that conversation is
deleted. There is no automatic conversation expiry. The route and repository
modules define the current persistence and API shapes.

## Development and verification

When adding a diagnostic tool, put its implementation in
`site_agents/assistant/tool_providers/diagnostics/`, assign it to a Settings
group, cap and redact its result, and cover its failure and timeout behavior.
When adding a library action, declare it in the shared host API with the
`assistant` profile, risk, setting key, summary, precondition, and undo behavior
where applicable. Add its Settings label and tests for recording, approval,
replay, and undo. The user must never be able to apply a model-written action
without a reviewed plan.

Coverage is in `tests/yaffo/site_agents/assistant/`, assistant route/task and
repository tests, `tests/scripts/test_build_assistant_knowledge.py`, and
`tests_js/assistant/`. The UI walkthrough is in
`yaffo_ui_tests/specs/assistant.yaml`. The most important regression checks are:

- Disabled tools and actions are absent from the model's available capabilities.
- A script's proposed mutations leave the library unchanged until approval.
- Approval replays frozen arguments and rejects expired or stale plans.
- Undo preserves later edits and reports partial execution.
- Filesystem diagnostics stay within named roots and network-blocked tests cover
  the assistant's tools and actions.
- The bundled knowledge and page catalogs match their sources.

For task queue behavior and scheduling, see [Task Queue Standards](task-queue.md).
For the shared Starlark host API, see [Automations](automations.md).
