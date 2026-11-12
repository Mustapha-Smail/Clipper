import pytest


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    """A fresh data folder and SQLite database for one test."""
    monkeypatch.setenv("CLIPPER_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CLIPPER_DATABASE_URL", raising=False)
    from clipper import db

    db.reset_engine()
    db.upgrade()
    yield tmp_path
    db.reset_engine()


@pytest.fixture()
def client(app_env):
    from fastapi.testclient import TestClient

    from clipper.api import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture()
def video(app_env):
    """A video row with two moments, as if it had been processed."""
    from clipper.db import session_scope
    from clipper.db.models import Moment, Video

    with session_scope() as db:
        row = Video(id="vid1", url=None, title="Test talk", uploader="Someone",
                    duration=300.0, width=1920, height=1080)
        row.moments = [
            Moment(position=1, start_s=10, end_s=40, title="First", hook="Hook one", caption="One"),
            Moment(position=2, start_s=60, end_s=95, title="Second", hook="Hook two", caption="Two"),
        ]
        db.add(row)
    (app_env / "sources" / "vid1").mkdir(parents=True)
    return "vid1"
