"""Unit tests for custom-automation run recording (background_tasks/automation_runs).

run_and_record runs a custom automation's sandboxed code and writes the run to the
Job table (tagged with automation_id) -- a COMPLETED job on success, a FAILED job
with the error on a bad script. Run against a throwaway SQLite DB; data_query is
faked so the script needn't hit real photo data.
"""
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from yaffo.background_tasks.automation_runs import record_run, run_and_record
from yaffo.db import db
from yaffo.db.models import (
    Automation,
    Job,
    JOB_STATUS_CANCELLED,
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess
    engine.dispose()


def _custom_automation(session, slug, code):
    automation = Automation(
        slug=slug, name=slug.title(), is_system=False, enabled=True,
        handler=None, published_code=code,
    )
    session.add(automation)
    session.commit()
    return automation


def test_successful_run_writes_completed_job(session, monkeypatch):
    monkeypatch.setattr(
        "yaffo.background_tasks.automation_sandbox.automation_actions.resolve_query",
        lambda s, q: [{"id": 1}, {"id": 2}],
    )
    automation = _custom_automation(
        session, "logger",
        'rows = data_query({"source": "media_items", "limit": 2})\n'
        'for r in rows:\n    print(r)',
    )

    job = run_and_record(session, automation, None)

    assert job.automation_id == automation.id
    assert job.name == automation.slug
    assert job.status == JOB_STATUS_COMPLETED
    assert job.completed_count == 1
    assert job.started_at is not None and job.completed_at is not None
    # captured print output is persisted on the job
    assert "output" in json.loads(job.job_data)
    assert len(json.loads(job.job_data)["output"]) == 2

    # and it's discoverable via the automation -> jobs relationship
    assert session.query(Job).filter_by(automation_id=automation.id).count() == 1


def test_report_progress_counts_are_kept_not_clobbered(session):
    """A custom script that calls report_progress keeps the counts it reported — the
    run finaliser must not overwrite completed_count with its single-unit default."""
    automation = _custom_automation(
        session, "progressive",
        "total = 5\nfor i in range(total):\n    report_progress(i + 1, total)\n",
    )

    job = run_and_record(session, automation, None)

    assert job.status == JOB_STATUS_COMPLETED
    assert job.task_count == 5
    assert job.completed_count == 5  # not 1


def test_bad_script_writes_failed_job(session):
    automation = _custom_automation(session, "broken", "this is ( not valid starlark")

    job = run_and_record(session, automation, None)

    assert job.status == JOB_STATUS_FAILED
    assert job.error_count == 1
    assert job.error
    assert job.automation_id == automation.id


def _system_automation(session, slug):
    automation = Automation(
        slug=slug, name=slug.title(), is_system=True, enabled=True, handler=slug,
    )
    session.add(automation)
    session.commit()
    return automation


def test_record_run_writes_completed_job(session):
    """A system automation's run is recorded as a COMPLETED Job with the work's
    summary in job_data, discoverable via jobs.automation_id. record_run hands the
    work a ProgressReporter bound to the run's Job, and the counts it reports land on
    the Job."""
    automation = _system_automation(session, "assign_location_name")
    reporters = []

    def work(progress_reporter):
        reporters.append(progress_reporter)
        progress_reporter.progress_update(5, 3, 0, 0)
        return "named 3/5 photo(s)"

    job = record_run(session, automation, work)

    assert len(reporters) == 1
    assert reporters[0].job_id == job.id
    assert job.automation_id == automation.id
    assert job.name == automation.slug
    assert job.status == JOB_STATUS_COMPLETED
    assert job.task_count == 5
    assert job.completed_count == 3
    assert job.started_at is not None and job.completed_at is not None
    assert json.loads(job.job_data)["output"] == "named 3/5 photo(s)"
    assert session.query(Job).filter_by(automation_id=automation.id).count() == 1


def test_record_run_captures_work_failure(session):
    """If the work raises, the run is a FAILED Job (error recorded, not re-raised).
    The work is still handed a ProgressReporter bound to the run's Job."""
    automation = _system_automation(session, "export_photo_tag")
    reporters = []

    def boom(progress_reporter) -> str:
        reporters.append(progress_reporter)
        raise RuntimeError("disk on fire")

    job = record_run(session, automation, boom)  # must not raise

    assert len(reporters) == 1
    assert reporters[0].job_id == job.id
    assert job.status == JOB_STATUS_FAILED
    assert "disk on fire" in job.error
    assert job.automation_id == automation.id
    assert job.completed_at is not None


def test_record_run_completes_empty_media_scope_without_calling_work(session):
    automation = _system_automation(session, "duplicate_scan")

    def work(_reporter):
        pytest.fail("empty scope should not execute automation work")

    job = record_run(session, automation, work, media_item_ids=[])

    assert job.automation_id == automation.id
    assert job.status == JOB_STATUS_COMPLETED
    assert job.started_at is not None and job.completed_at is not None
    assert json.loads(job.job_data)["output"] == "No indexed media items to process"


def _cancel_elsewhere(session, job_id):
    """Cancel `job_id` as the Cancel button does: a commit from another session."""
    with Session(session.get_bind()) as other:
        other.get(Job, job_id).status = JOB_STATUS_CANCELLED
        other.commit()


def test_record_run_stops_at_the_next_check_and_stays_cancelled(session):
    """A cancel issued mid-run stops run_with_progress at its next check, counts the
    rest as cancelled, and isn't overwritten by the COMPLETED finalisation."""
    automation = _system_automation(session, "classify_labels")
    processed = []

    def work(reporter):
        def process(item):
            processed.append(item)
            if item == 3:
                _cancel_elsewhere(session, reporter.job_id)
        finished = reporter.run_with_progress(list(range(25)), process)
        return f"finished={finished}"

    job = record_run(session, automation, work)

    session.refresh(job)
    assert processed == list(range(10))  # checks every 10 items
    assert job.status == JOB_STATUS_CANCELLED
    assert (job.task_count, job.completed_count, job.cancelled_count) == (25, 10, 15)
    assert json.loads(job.job_data) == {"output": "finished=False"}
    assert job.completed_at is not None


def test_record_run_failure_after_a_cancel_stays_cancelled(session):
    automation = _system_automation(session, "export_photo_tag")

    def work(reporter):
        _cancel_elsewhere(session, reporter.job_id)
        raise RuntimeError("stopped mid-write")

    job = record_run(session, automation, work)

    session.refresh(job)
    assert job.status == JOB_STATUS_CANCELLED
    assert job.error == "stopped mid-write"


def test_custom_run_cancelled_while_it_runs_is_recorded_cancelled(session, monkeypatch):
    automation = _custom_automation(session, "canceller", 'data_query({"source": "media_items"})\nprint("done")')

    def cancel_then_answer(s, q):
        job = session.query(Job).filter_by(automation_id=automation.id).one()
        _cancel_elsewhere(session, job.id)
        return []
    monkeypatch.setattr(
        "yaffo.background_tasks.automation_sandbox.automation_actions.resolve_query", cancel_then_answer)

    job = run_and_record(session, automation, None)

    session.refresh(job)
    assert job.status == JOB_STATUS_CANCELLED
    assert job.error is None


def test_a_retried_task_records_on_the_same_job_and_a_finished_one_does_not_rerun(session):
    """The queue re-runs a task stranded by a crash with the same id; record_run keys
    the run Job by it, so the retry reuses that Job instead of opening a second."""
    automation = _system_automation(session, "classify_labels")
    stranded = Job(id="task-1", name=automation.slug, status="RUNNING", automation_id=automation.id,
                   task_count=100, completed_count=30)
    session.add(stranded)
    session.commit()
    ran = []

    job = record_run(session, automation, lambda reporter: ran.append(1) or "done", job_id="task-1")
    again = record_run(session, automation, lambda reporter: ran.append(2) or "done", job_id="task-1")

    assert job.id == "task-1" and job.status == JOB_STATUS_COMPLETED
    assert again is None and ran == [1]
    assert session.query(Job).filter_by(automation_id=automation.id).count() == 1


def test_custom_run_cancel_kills_the_script_through_the_sandbox(session, monkeypatch):
    """Scenario 4: a cancel while a script is still running reaches the sandbox via
    the executor's should_cancel (progress.is_cancelled), which kills the script.
    Without that wiring the script would run to the 60s sandbox limit."""
    import time
    automation = _custom_automation(
        session, "spinner",
        'data_query({"source": "media_items"})\n'
        'def spin():\n    n = 0\n    for i in range(1000000000):\n        n += i\n    return n\n'
        'spin()',
    )

    def cancel_then_answer(s, q):
        job = session.query(Job).filter_by(automation_id=automation.id).one()
        _cancel_elsewhere(session, job.id)
        return []
    monkeypatch.setattr(
        "yaffo.background_tasks.automation_sandbox.automation_actions.resolve_query", cancel_then_answer)

    started = time.monotonic()
    job = run_and_record(session, automation, None)
    elapsed = time.monotonic() - started

    session.refresh(job)
    assert job.status == JOB_STATUS_CANCELLED
    assert elapsed < 15  # killed at the next ~1s poll, not at the sandbox time limit
