import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.core.config import settings
from backend.app.db.base import Base
from backend.app.db.session import get_db
from backend.app.main import app
from backend.app.models import Transaction  # noqa: F401


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_db():
        db = testing_sessions()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr("backend.app.api.routes.webhooks.process_payment_event.delay", lambda event_id: None)
    old_secret = settings.webhook_secret
    settings.webhook_secret = "test-secret"
    with TestClient(app) as test_client:
        test_client.testing_sessions = testing_sessions
        yield test_client
    settings.webhook_secret = old_secret
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()
