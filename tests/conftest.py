import calendar

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import CSRF_HEADER, CSRF_VALUE, create_app
from app.profile import Profile

START = float(calendar.timegm((2026, 10, 3, 12, 0, 0)))
PASSWORD = "correct horse battery"
JOB = ("We need a chatbot for our website that qualifies leads and sends them to HubSpot. "
       "Budget is $500 and it must be live within 3 weeks. Python is fine.")


class Clock:
    def __init__(self, t=START):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def settings(tmp_path):
    return Settings(db_path=str(tmp_path / "app.db"), monthly_limit=3)


@pytest.fixture
def app(settings, clock):
    return create_app(settings, mode="offline", clock=clock)


def new_client(app, email="sam@example.com", password=PASSWORD, signup=True):
    c = TestClient(app, headers={CSRF_HEADER: CSRF_VALUE})
    if signup:
        assert c.post("/api/signup", json={"email": email, "password": password}).status_code == 201
    return c


@pytest.fixture
def client(app):
    return new_client(app)


PROFILE = {"name": "Sam Lee", "headline": "Python developer for AI automation", "skills": ["Python", "FastAPI"],
           "proof_points": ["Built a lead qualification chatbot in Python and FastAPI that scores leads and posts them to a CRM.",
                            "Built a PDF question answering tool whose answers cite the page."], "tone": "friendly"}


@pytest.fixture
def ready(client):
    assert client.put("/api/profile", json=PROFILE).status_code == 200
    return client


@pytest.fixture
def profile():
    return Profile(**PROFILE).cleaned()
