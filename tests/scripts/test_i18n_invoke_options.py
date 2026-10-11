"""Both translation Invoke tasks forward CLI configuration without running it."""
import shlex

import pytest

import tasks

pytestmark = pytest.mark.unit


class RecordingContext:
    def __init__(self):
        self.commands = []

    def run(self, command, **kwargs):
        self.commands.append(shlex.split(command))


@pytest.mark.parametrize("all_locales", [False, True])
def test_invoke_tasks_forward_the_cli_model_and_timeout(monkeypatch, all_locales):
    context = RecordingContext()
    monkeypatch.setattr(tasks, "_translation_locales", lambda: ["de"])
    options = {"engine": "llm", "llm_cli": "claude", "llm_model": "custom model", "llm_timeout": 45}
    if all_locales:
        tasks.i18n_translate_all.body(context, **options)
    else:
        tasks.i18n_translate.body(context, locale="de", **options)
    command = next(command for command in context.commands if "translate" in command)
    assert command[command.index("--llm-cli") + 1] == "claude"
    assert command[command.index("--llm-model") + 1] == "custom model"
    assert command[command.index("--llm-timeout") + 1] == "45"
    assert command[command.index("--engine") + 1] == "llm"
