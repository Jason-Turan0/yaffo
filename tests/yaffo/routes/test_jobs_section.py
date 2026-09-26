"""The generic job section fragment (GET /jobs/section)."""
import pytest

from yaffo.db import db
from yaffo.db.models import JOB_STATUS_RUNNING, Job

pytestmark = pytest.mark.unit


def test_jobs_section_renders_the_named_jobs(app, client):
    with app.app_context():
        db.session.add(Job(id="j1", name="index_photos", status=JOB_STATUS_RUNNING, task_count=4))
        db.session.commit()

    response = client.get("/jobs/section?job_name=index_photos&has_results=true")

    assert response.status_code == 200
    assert 'id="job-progress-section"' in response.get_data(as_text=True)


def test_jobs_section_needs_a_job_name(client):
    assert client.get("/jobs/section").status_code == 400


def test_a_job_without_a_message_still_renders_its_card(app, client):
    with app.app_context():
        db.session.add(Job(id="sync", name="file_sync", status=JOB_STATUS_RUNNING, task_count=1, message=None))
        db.session.commit()

    response = client.get("/jobs/section?job_name=file_sync")

    assert response.status_code == 200 and 'id="job-sync"' in response.get_data(as_text=True)
