import pytest

from yaffo.app import create_app
from yaffo.db import db


@pytest.fixture
def app(tmp_path):
    application = create_app(db_path=tmp_path / "test.db", config={"TESTING": True})
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()
