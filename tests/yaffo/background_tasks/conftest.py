"""Shared fixtures for the background-tasks unit tests."""
import pytest


class FakeProgressReporter:
    """Stand-in for ProgressReporter that records the calls a task makes and, like the
    real thing, drives `item_processor` over the items so the task's side effects still
    happen. Tests assert against `run_with_progress_calls` / `progress_update_calls` to
    prove the task reported progress."""

    def __init__(self):
        self.run_with_progress_calls: list[list] = []
        self.progress_update_calls: list[tuple[int, int, int, int]] = []

    def run_with_progress(self, items, item_processor, percentage=0.05):
        self.run_with_progress_calls.append(list(items))
        for item in items:
            item_processor(item)

    def progress_update(self, task_count, completed_count, cancelled, error_count):
        self.progress_update_calls.append((task_count, completed_count, cancelled, error_count))

    @property
    def called(self) -> bool:
        return bool(self.run_with_progress_calls or self.progress_update_calls)


@pytest.fixture
def progress_reporter() -> FakeProgressReporter:
    return FakeProgressReporter()