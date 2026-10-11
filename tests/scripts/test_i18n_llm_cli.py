"""CLI translation transport, tested without launching a model or changing catalogs."""
import json
import signal
import subprocess
from pathlib import Path

import pytest

from scripts import i18n_llm_cli as cli

pytestmark = pytest.mark.unit

ENTRIES = [{"id": "browser:common.greeting", "source": "Hello {{name}}", "context": "common.greeting"}]
TRANSLATIONS = {"translations": [{"id": ENTRIES[0]["id"], "value": "Hallo {{name}}"}]}


@pytest.fixture
def process(monkeypatch):
    calls = []
    behavior = {"payload": TRANSLATIONS, "returncode": 0, "timeout": False}

    class Process:
        pid = 4321

        def __init__(self, command, **kwargs):
            self.command = command
            self.options = kwargs
            self.returncode = behavior["returncode"]
            self.waited = False
            calls.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def communicate(self, prompt, timeout):
            self.prompt = prompt
            self.timeout = timeout
            if behavior["timeout"]:
                raise subprocess.TimeoutExpired(self.command, timeout)
            if "--output-last-message" in self.command:
                path = Path(self.command[self.command.index("--output-last-message") + 1])
                path.write_text(json.dumps(behavior["payload"]), encoding="utf-8")
                self.options["stdout"].write("CLI progress is not the result.\n")
            else:
                self.options["stdout"].write(json.dumps({"is_error": False, "structured_output": behavior["payload"]}))

        def wait(self, timeout=None):
            self.waited = True
            return self.returncode

    monkeypatch.setattr(cli.subprocess, "Popen", Process)
    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/cli/{name}")
    return calls, behavior


def test_codex_uses_stdin_and_reads_only_the_structured_final_message(process):
    calls, _ = process
    translated = cli.translate_with_cli(ENTRIES, "de", cli.CliOptions(model="custom-model", timeout_seconds=45))
    assert translated == {ENTRIES[0]["id"]: "Hallo {{name}}"}
    child = calls[0]
    assert child.command[:2] == ["/cli/codex", "exec"] and child.command[-1] == "-"
    assert child.command[child.command.index("--sandbox") + 1] == "read-only"
    assert child.command[child.command.index("--model") + 1] == "custom-model"
    assert "Hello {{name}}" in child.prompt and '"target_locale": "de"' in child.prompt
    assert not any("Hello" in argument for argument in child.command)
    assert child.timeout == 45
    assert not Path(child.options["cwd"]).exists()


def test_claude_uses_its_structured_output_envelope_and_disables_tools(process):
    calls, _ = process
    assert cli.translate_with_cli(ENTRIES, "de", cli.CliOptions(cli="claude")) == {
        ENTRIES[0]["id"]: "Hallo {{name}}"}
    command = calls[0].command
    assert command[0] == "/cli/claude" and "--print" in command
    assert command[command.index("--tools") + 1] == ""
    assert command[command.index("--disallowedTools") + 1] == "mcp__*"
    assert "--no-session-persistence" in command
    assert "--model" not in command


def test_auto_uses_claude_when_codex_is_not_installed(process, monkeypatch):
    calls, _ = process
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/cli/claude" if name == "claude" else None)
    cli.translate_with_cli(ENTRIES, "de")
    assert calls[0].command[0] == "/cli/claude"


def test_missing_cli_fails_before_starting_a_process(process, monkeypatch):
    calls, _ = process
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    with pytest.raises(cli.CliUnavailableError, match="Install and sign in"):
        cli.translate_with_cli(ENTRIES, "de")
    assert calls == []


def test_timeout_stops_the_process_group_and_reaps_the_cli(process, monkeypatch):
    calls, behavior = process
    behavior["timeout"] = True
    killed = []
    if cli.os.name == "nt":
        def stop_tree(command, **kwargs):
            killed.append(command)
            return subprocess.CompletedProcess(command, 0)
        monkeypatch.setattr(cli.subprocess, "run", stop_tree)
        expected = [["taskkill", "/PID", "4321", "/T", "/F"]]
    else:
        monkeypatch.setattr(cli.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
        expected = [(4321, signal.SIGKILL)]
    with pytest.raises(ValueError, match="timed out after 7 seconds"):
        cli.translate_with_cli(ENTRIES, "de", cli.CliOptions(timeout_seconds=7))
    assert killed == expected and calls[0].waited
    assert not Path(calls[0].options["cwd"]).exists()


def test_cli_failure_is_not_mistaken_for_a_translation(process):
    _, behavior = process
    behavior["returncode"] = 1
    with pytest.raises(ValueError, match="failed.*exit 1"):
        cli.translate_with_cli(ENTRIES, "de")


@pytest.mark.parametrize("payload", [
    {"translations": [{"id": "unknown", "value": "Hallo"}]},
    {"translations": TRANSLATIONS["translations"] * 2},
    {"translations": [{"id": ENTRIES[0]["id"], "value": 42}]},
    {"translations": [{"id": ENTRIES[0]["id"], "value": ["Hallo", 42]}]},
    [],
])
def test_invalid_responses_are_rejected(process, payload):
    _, behavior = process
    behavior["payload"] = payload
    with pytest.raises(ValueError, match="LLM CLI returned"):
        cli.translate_with_cli(ENTRIES, "de")


def test_response_size_and_timeout_are_bounded(process, monkeypatch):
    monkeypatch.setattr(cli, "MAX_RESPONSE_BYTES", 10)
    with pytest.raises(ValueError, match="size limit"):
        cli.translate_with_cli(ENTRIES, "de")
    with pytest.raises(ValueError, match="timeout must be greater"):
        cli.translate_with_cli(ENTRIES, "de", cli.CliOptions(timeout_seconds=0))
