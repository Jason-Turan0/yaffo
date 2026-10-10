"""The static check of a Starlark adapter: metadata, contract rules, and endpoints.

Run when adapter code is written and again at publish (and on every preset seed), so
nothing about an adapter has to be learned by running it:

- `ADAPTER` is assigned once at top level, as a literal except for `hooks`, whose
  values are bare names of top-level `def`s. Its metadata is extracted with the
  hooks as their names.
- The contract rules: `contract == CURRENT_CONTRACT`, required and known hooks per
  role, options ↔ `choices`, capabilities ↔ hooks, and no top-level host calls.
- The endpoint manifest: every `web_request` / `web_upload_media` call site, with
  the connection the literal `CONNECTION`, a literal method, and a URL that's a
  literal, a module constant, or `url_template(<literal>, ...)` (concatenations of
  those included). A URL the checker can't resolve to one of those, such as a
  `nextLink` taken from a response or a stored cursor, is recorded as a
  **follow-up** call site: at run time it's only allowed for URLs a granted call
  returned (see web.py). Building a URL by gluing a variable onto a literal is an
  error: that's what `url_template` is for.

Starlark's syntax is a subset of Python's, so this walks Python's `ast` of the code
after the Starlark parser has accepted it.
"""
from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator

from yaffo.background_tasks.automation_sandbox.starlark_runner import validate_starlark
from yaffo.db.models import SYNC_ROLE_SOURCE, SYNC_ROLE_TARGET
from yaffo.sync.options import OPTION_REMOTE_COLLECTION, OPTION_REMOTE_FOLDER, OPTION_TYPES
from yaffo.sync.providers import get_provider, is_base_url
from yaffo.sync.schemas import GRANT_ENDPOINT, GRANT_FOLLOWUP, RISK_READ, RISK_WRITE

CURRENT_CONTRACT = 1

WEB_FUNCTIONS = ("web_request", "web_upload_media")
HOST_FUNCTIONS = (*WEB_FUNCTIONS, "url_template", "print")
CONNECTION_NAME = "CONNECTION"

REQUIRED_HOOKS = {SYNC_ROLE_SOURCE: {"list_page"}, SYNC_ROLE_TARGET: {"push"}}
OPTIONAL_HOOKS = {SYNC_ROLE_SOURCE: {"resolve_download", "choices"},
                  SYNC_ROLE_TARGET: {"remove", "update", "list_remote", "choices"}}
CAPABILITY_HOOKS = {"remove": "remove", "update": "update", "reconcile": "list_remote"}
READ_METHODS = ("GET", "HEAD")

_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

_OPTION_SCHEMA = {
    "type": "object",
    "required": ["type", "label"],
    "additionalProperties": False,
    "properties": {
        "type": {"enum": [t for t in OPTION_TYPES if t != "local_folder"]},
        "label": {"type": "string", "minLength": 1},
        "help": {"type": "string"},
        "required": {"type": "boolean"},
        "create": {"type": "boolean"},                 # remote_collection: offer "New …" with a name
        "default": {},
        "choices": {"type": "array", "items": {
            "type": "object", "required": ["value", "label"], "additionalProperties": False,
            "properties": {"value": {}, "label": {"type": "string"}}}},
    },
}

METADATA_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["contract", "role", "provider", "title", "hooks"],
    "properties": {
        "contract": {"type": "integer"},
        "role": {"enum": [SYNC_ROLE_SOURCE, SYNC_ROLE_TARGET]},
        "provider": {"type": "string", "minLength": 1},
        "title": {"type": "string", "minLength": 1},
        "description": {"type": "string"},
        "scopes": {"type": "array", "items": {"type": "string"}},
        "options": {"type": "object", "additionalProperties": _OPTION_SCHEMA},
        "hooks": {"type": "object", "additionalProperties": {"type": "string"}},
        "capabilities": {"type": "object"},
        "connection": {"type": "object"},
    },
}
_METADATA_VALIDATOR = Draft202012Validator(METADATA_SCHEMA)


@dataclass(frozen=True)
class AdapterProblem:
    code: str
    message: str
    line: int | None = None

    def __str__(self) -> str:
        return f"line {self.line}: {self.message}" if self.line else self.message


@dataclass
class AdapterAnalysis:
    code_hash: str
    metadata: dict[str, Any] = field(default_factory=dict)
    manifest: list[dict[str, Any]] = field(default_factory=list)
    problems: list[AdapterProblem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def code_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


class _Dynamic:
    """A URL the checker can't resolve statically (a follow-up URL)."""


DYNAMIC = _Dynamic()


class _Analyzer:
    def __init__(self, tree: ast.Module, analysis: AdapterAnalysis):
        self.tree = tree
        self.analysis = analysis
        self.defs: dict[str, ast.FunctionDef] = {}
        self.constants: dict[str, ast.expr] = {}
        self.adapter_node: ast.expr | None = None
        self.adapter_line: int | None = None

    def problem(self, code: str, message: str, node: ast.AST | None = None) -> None:
        self.analysis.problems.append(AdapterProblem(code, message, getattr(node, "lineno", None)))

    # ---- top level -----------------------------------------------------------

    def scan_top_level(self) -> None:
        adapter_assignments = 0
        for statement in self.tree.body:
            if isinstance(statement, ast.FunctionDef):
                self.defs[statement.name] = statement
            elif isinstance(statement, ast.Assign) and len(statement.targets) == 1 \
                    and isinstance(statement.targets[0], ast.Name):
                name = statement.targets[0].id
                if name == "ADAPTER":
                    adapter_assignments += 1
                    self.adapter_node = statement.value
                    self.adapter_line = statement.lineno
                else:
                    self.constants[name] = statement.value
            elif isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant) \
                    and isinstance(statement.value.value, str):
                continue  # a docstring
            elif isinstance(statement, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                self.problem("top_level_assignment", "top-level assignments must name a single constant",
                             statement)
            else:
                self.problem("top_level_statement",
                             "an adapter's top level may only define constants and functions", statement)
            for node in ast.walk(statement):
                if isinstance(statement, ast.FunctionDef):
                    break
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                        and node.func.id in HOST_FUNCTIONS and node.func.id != "url_template":
                    self.problem("top_level_host_call", f"{node.func.id}() can't run at the top level", node)
        if adapter_assignments == 0:
            self.problem("no_adapter", "the module must assign ADAPTER")
        elif adapter_assignments > 1:
            self.problem("adapter_reassigned", "ADAPTER must be assigned exactly once")

    # ---- metadata ------------------------------------------------------------

    def extract_metadata(self) -> None:
        node = self.adapter_node
        if node is None:
            return
        if not isinstance(node, ast.Dict):
            self.problem("adapter_not_literal", "ADAPTER must be a dict literal", node)
            return
        metadata: dict[str, Any] = {}
        for key_node, value_node in zip(node.keys, node.values):
            if not isinstance(key_node, ast.Constant) or not isinstance(key_node.value, str):
                self.problem("adapter_not_literal", "ADAPTER keys must be strings", key_node or node)
                continue
            key = key_node.value
            if key == "hooks":
                metadata["hooks"] = self.extract_hooks(value_node)
                continue
            try:
                metadata[key] = ast.literal_eval(value_node)
            except ValueError:
                self.problem("adapter_not_literal", f"ADAPTER[{key!r}] must be a literal", value_node)
        self.analysis.metadata = metadata

    def extract_hooks(self, node: ast.expr) -> dict[str, str]:
        if not isinstance(node, ast.Dict):
            self.problem("hooks_not_literal", "ADAPTER['hooks'] must be a dict literal", node)
            return {}
        hooks: dict[str, str] = {}
        for key_node, value_node in zip(node.keys, node.values):
            if not isinstance(key_node, ast.Constant) or not isinstance(key_node.value, str):
                self.problem("hooks_not_literal", "hook names must be strings", node)
                continue
            if not isinstance(value_node, ast.Name) or value_node.id not in self.defs:
                self.problem("hook_not_a_function",
                             f"hook {key_node.value!r} must be the name of a top-level def", value_node)
                continue
            hooks[key_node.value] = value_node.id
        return hooks

    def check_rules(self) -> None:
        metadata = self.analysis.metadata
        if not metadata:
            return
        for error in sorted(_METADATA_VALIDATOR.iter_errors(metadata), key=lambda e: list(e.path)):
            where = "/".join(str(part) for part in error.absolute_path) or "ADAPTER"
            self.problem("metadata_invalid", f"{where}: {error.message}", self.adapter_node)
        if metadata.get("contract") != CURRENT_CONTRACT:
            self.problem("contract_outdated",
                         f"ADAPTER['contract'] must be {CURRENT_CONTRACT} (got {metadata.get('contract')!r})",
                         self.adapter_node)
        role = metadata.get("role")
        if role not in REQUIRED_HOOKS:
            return
        provider = metadata.get("provider")
        if provider == "custom" or "connection" in metadata:
            self.problem("custom_not_supported", "custom adapters with their own connection block "
                         "aren't supported yet", self.adapter_node)
        elif get_provider(str(provider)) is None:
            self.problem("unknown_provider", f"unknown provider {provider!r}", self.adapter_node)
        hooks = set(metadata.get("hooks", {}))
        for missing in sorted(REQUIRED_HOOKS[role] - hooks):
            self.problem("missing_hook", f"a {role} adapter must register the {missing!r} hook", self.adapter_node)
        for unknown in sorted(hooks - REQUIRED_HOOKS[role] - OPTIONAL_HOOKS[role]):
            self.problem("unknown_hook", f"{unknown!r} isn't a {role} hook", self.adapter_node)
        options = metadata.get("options") or {}
        remote = [k for k, spec in options.items() if isinstance(spec, dict)
                  and spec.get("type") in (OPTION_REMOTE_FOLDER, OPTION_REMOTE_COLLECTION)]
        if remote and "choices" not in hooks:
            self.problem("choices_missing", f"option {remote[0]!r} needs a 'choices' hook", self.adapter_node)
        for key, spec in options.items():
            if isinstance(spec, dict) and spec.get("create") and spec.get("type") != OPTION_REMOTE_COLLECTION:
                self.problem("create_not_collection", f"option {key!r}: only a remote_collection can offer "
                             "a new one", self.adapter_node)
        if "choices" in hooks and not remote:
            self.problem("choices_unused", "a 'choices' hook needs a remote_folder or remote_collection option",
                         self.adapter_node)
        capabilities = metadata.get("capabilities") or {}
        if role == SYNC_ROLE_SOURCE and capabilities:
            self.problem("capabilities_on_source", "only targets declare capabilities", self.adapter_node)
        for capability, hook in CAPABILITY_HOOKS.items():
            if role != SYNC_ROLE_TARGET:
                break
            if capabilities.get(capability) and hook not in hooks:
                self.problem("capability_without_hook", f"capability {capability!r} needs the {hook!r} hook",
                             self.adapter_node)
            if hook in hooks and not capabilities.get(capability):
                self.problem("hook_without_capability", f"the {hook!r} hook needs capability {capability!r}",
                             self.adapter_node)

    # ---- endpoints -----------------------------------------------------------

    def resolve(self, node: ast.expr, scope: ast.FunctionDef | None, depth: int = 0) -> list[Any]:
        """Every value `node` can take: strings (literal URLs or templates), or
        DYNAMIC. Raises _Concatenation for a literal glued to a dynamic part."""
        if depth > 20:
            return [DYNAMIC]
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            lefts, rights = self.resolve(node.left, scope, depth + 1), self.resolve(node.right, scope, depth + 1)
            if any(v is DYNAMIC for v in lefts + rights):
                raise _Concatenation(node)
            return [left + right for left in lefts for right in rights]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "url_template":
            if not node.args:
                raise _Concatenation(node)
            templates = self.resolve(node.args[0], scope, depth + 1)
            if any(v is DYNAMIC for v in templates):
                raise _Concatenation(node)
            return templates
        if isinstance(node, ast.Name):
            assigned = self.local_assignments(scope, node.id) if scope is not None else None
            if assigned is not None:
                values: list[Any] = []
                for value in assigned:
                    try:
                        values.extend(self.resolve(value, scope, depth + 1))
                    except _Concatenation:
                        raise
                return values or [DYNAMIC]
            if node.id in self.constants:
                return self.resolve(self.constants[node.id], None, depth + 1)
            return [DYNAMIC]
        return [DYNAMIC]

    @staticmethod
    def local_assignments(scope: ast.FunctionDef, name: str) -> list[ast.expr] | None:
        """The values assigned to a local name, or None if it isn't local (a
        parameter counts as local, with a dynamic value)."""
        params = {a.arg for a in scope.args.args + scope.args.kwonlyargs}
        values: list[ast.expr] = []
        found = name in params
        for node in ast.walk(scope):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == name:
                        values.append(node.value)
                        found = True
            elif isinstance(node, (ast.For, ast.comprehension)) and isinstance(node.target, ast.Name) \
                    and node.target.id == name:
                found = True
                values.append(ast.Name(id="__dynamic__"))
        if not found:
            return None
        if name in params:
            values.append(ast.Name(id="__dynamic__"))
        return values

    def call_argument(self, call: ast.Call, index: int, keyword: str) -> ast.expr | None:
        if len(call.args) > index:
            return call.args[index]
        return next((k.value for k in call.keywords if k.arg == keyword), None)

    def detect_endpoints(self) -> None:
        provider = get_provider(str(self.analysis.metadata.get("provider", "")))
        seen: set[tuple[str, str | None, str]] = set()
        for scope in self.defs.values():
            for node in ast.walk(scope):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                    continue
                if node.func.id == "url_template":
                    self.check_template_call(node, scope)
                if node.func.id not in WEB_FUNCTIONS:
                    continue
                connection = self.call_argument(node, 0, "connection")
                if not (isinstance(connection, ast.Name) and connection.id == CONNECTION_NAME):
                    self.problem("connection_not_literal",
                                 f"{node.func.id}() must be called with CONNECTION as its connection", node)
                    continue
                method_node = self.call_argument(node, 1, "method")
                try:
                    methods = self.resolve(method_node, scope) if method_node is not None else [DYNAMIC]
                except _Concatenation:
                    methods = [DYNAMIC]
                if any(m is DYNAMIC for m in methods):
                    self.problem("method_not_literal", f"{node.func.id}() needs a literal method", node)
                    continue
                url_node = self.call_argument(node, 2, "url")
                if url_node is None:
                    self.problem("url_missing", f"{node.func.id}() needs a URL", node)
                    continue
                try:
                    urls = self.resolve(url_node, scope)
                except _Concatenation as e:
                    self.problem("url_concatenation",
                                 "build URLs with url_template(), not by adding strings to values", e.node)
                    continue
                for method in methods:
                    method = method.upper()
                    risk = RISK_READ if method in READ_METHODS else RISK_WRITE
                    for url in urls:
                        if url is DYNAMIC:
                            entry = (method, None, GRANT_FOLLOWUP)
                        else:
                            if not self.check_url(url, provider, node):
                                continue
                            entry = (method, url, GRANT_ENDPOINT)
                        if entry in seen:
                            continue
                        seen.add(entry)
                        self.analysis.manifest.append({"method": entry[0], "url_pattern": entry[1],
                                                       "grant_kind": entry[2], "risk": risk,
                                                       "line": node.lineno})

    def check_template_call(self, node: ast.Call, scope: ast.FunctionDef) -> None:
        if not node.args:
            self.problem("template_missing", "url_template() needs a template", node)
            return
        try:
            templates = self.resolve(node.args[0], scope)
        except _Concatenation:
            templates = [DYNAMIC]
        if any(t is DYNAMIC for t in templates):
            self.problem("template_not_literal", "url_template() needs a literal template", node)

    def check_url(self, url: str, provider, node: ast.AST) -> bool:
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.netloc or _PLACEHOLDER.search(parts.netloc):
            self.problem("url_not_https", f"{url!r} must be an https URL with a literal host", node)
            return False
        if provider is not None and not is_base_url(provider, url):
            self.problem("url_outside_provider", f"{url!r} isn't on {provider.label}'s hosts", node)
            return False
        return True


class _Concatenation(Exception):
    def __init__(self, node: ast.AST):
        super().__init__("concatenation")
        self.node = node


def analyze_adapter(code: str) -> AdapterAnalysis:
    analysis = AdapterAnalysis(code_hash=code_hash(code))
    parse_error = validate_starlark(code, filename="adapter.star")
    if parse_error:
        analysis.problems.append(AdapterProblem("syntax", parse_error))
        return analysis
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        analysis.problems.append(AdapterProblem("syntax", str(e), e.lineno))
        return analysis
    analyzer = _Analyzer(tree, analysis)
    analyzer.scan_top_level()
    analyzer.extract_metadata()
    analyzer.check_rules()
    if analysis.metadata:
        analyzer.detect_endpoints()
    return analysis


def placeholder_names(template: str) -> list[str]:
    return _PLACEHOLDER.findall(template)


def pattern_regex(url_pattern: str) -> re.Pattern[str]:
    """A grant's URL pattern as a regex over scheme://host/path (no query).
    `{path}` and `{..._path}` match several segments, other placeholders one."""
    base = url_pattern.split("?", 1)[0]
    out, last = [], 0
    for match in _PLACEHOLDER.finditer(base):
        out.append(re.escape(base[last:match.start()]))
        name = match.group(1)
        out.append(r"[^?#]+" if name == "path" or name.endswith("_path") else r"[^/?#]+")
        last = match.end()
    out.append(re.escape(base[last:]))
    return re.compile("^" + "".join(out) + "$", re.IGNORECASE)
