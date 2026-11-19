import pytest


@pytest.fixture()
def tmp_data(tmp_path, monkeypatch):
    """Isolated data dir + fresh DB for each test that touches storage."""
    monkeypatch.setenv("GI_DATA_DIR", str(tmp_path))
    from garmin_insights.storage import db

    db.reset_connections()
    yield tmp_path
    db.reset_connections()
