import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import CSRF_HEADER, CSRF_VALUE, create_app
from tests.conftest import JOB, PASSWORD, PROFILE, new_client


def generate(c, job=JOB, **extra):
    return c.post("/api/proposals/generate", json={"job_text": job, **extra})


# --- CSRF -----------------------------------------------------------------------------------------

def test_a_change_without_the_custom_header_is_refused(app):
    bare = TestClient(app)
    for method, path in [("post", "/api/signup"), ("post", "/api/login"), ("post", "/api/logout"), ("put", "/api/profile"), ("delete", "/api/proposals/1")]:
        r = bare.request(method.upper(), path, json={"email": "a@b.co", "password": PASSWORD})
        assert r.status_code == 403 and CSRF_HEADER in r.json()["detail"]


def test_reading_does_not_need_the_header(app):
    assert TestClient(app).get("/api/health").status_code == 200


def test_a_wrong_header_value_is_refused(app):
    assert TestClient(app, headers={CSRF_HEADER: "anything"}).post("/api/login", json={"email": "a@b.co", "password": "x"}).status_code == 403


# --- signup, login, sessions ------------------------------------------------------------------------

def test_signup_logs_you_in_and_lowercases_the_email(app):
    c = TestClient(app, headers={CSRF_HEADER: CSRF_VALUE})
    assert c.post("/api/signup", json={"email": "  Sam@Example.COM ", "password": PASSWORD}).json() == {"email": "sam@example.com"}
    assert c.get("/api/me").json()["email"] == "sam@example.com"


@pytest.mark.parametrize("email, password, message", [
    ("not-an-email", PASSWORD, "valid email"), ("a@b.co", "short", "at least 10"), ("a@b.co", "A@B.CO", "at least 10"),
    ("longer@example.com", "longer@example.com", "cannot be your email"),
])
def test_signup_rules(app, email, password, message):
    r = TestClient(app, headers={CSRF_HEADER: CSRF_VALUE}).post("/api/signup", json={"email": email, "password": password})
    assert r.status_code == 422 and message in r.json()["detail"]


def test_an_email_cannot_sign_up_twice(app, client):
    r = TestClient(app, headers={CSRF_HEADER: CSRF_VALUE}).post("/api/signup", json={"email": "SAM@example.com", "password": PASSWORD})
    assert r.status_code == 409


def test_the_password_is_stored_only_as_a_hash(settings, client):
    row = sqlite3.connect(settings.db_path).execute("SELECT password_hash FROM users").fetchone()[0]
    assert row.startswith("scrypt$") and PASSWORD not in row


def test_the_session_cookie_is_httponly_and_samesite_and_stored_hashed(settings, app):
    c = TestClient(app, headers={CSRF_HEADER: CSRF_VALUE})
    r = c.post("/api/signup", json={"email": "sam@example.com", "password": PASSWORD})
    header = r.headers["set-cookie"].lower()
    assert "httponly" in header and "samesite=lax" in header and "secure" not in header
    token = c.cookies.get("sid")
    stored = [row[0] for row in sqlite3.connect(settings.db_path).execute("SELECT token_hash FROM sessions")]
    assert token not in stored and len(stored) == 1


def test_the_cookie_is_secure_when_configured(tmp_path, clock):
    app = create_app(Settings(db_path=str(tmp_path / "s.db"), cookie_secure=True), mode="offline", clock=clock)
    r = TestClient(app, headers={CSRF_HEADER: CSRF_VALUE}).post("/api/signup", json={"email": "a@b.co", "password": PASSWORD})
    assert "secure" in r.headers["set-cookie"].lower()


def test_wrong_password_and_unknown_email_look_the_same(app, client):
    wrong = new_client(app, signup=False).post("/api/login", json={"email": "sam@example.com", "password": "nope nope nope"})
    unknown = new_client(app, signup=False).post("/api/login", json={"email": "nobody@example.com", "password": "nope nope nope"})
    assert wrong.status_code == unknown.status_code == 401 and wrong.json() == unknown.json()


def test_login_works_with_the_right_password(app, client):
    c = new_client(app, signup=False)
    assert c.post("/api/login", json={"email": "SAM@example.com", "password": PASSWORD}).status_code == 200
    assert c.get("/api/me").status_code == 200


def test_five_failures_lock_the_email_even_for_the_right_password_until_the_window_passes(app, client, clock):
    c = new_client(app, signup=False)
    for _ in range(5):
        assert c.post("/api/login", json={"email": "sam@example.com", "password": "wrong wrong wrong"}).status_code == 401
    locked = c.post("/api/login", json={"email": "sam@example.com", "password": PASSWORD})
    assert locked.status_code == 429 and "Too many" in locked.json()["detail"]
    clock.advance(901)
    assert c.post("/api/login", json={"email": "sam@example.com", "password": PASSWORD}).status_code == 200


def test_a_good_login_resets_the_failure_count(app, client):
    c = new_client(app, signup=False)
    for _ in range(4):
        c.post("/api/login", json={"email": "sam@example.com", "password": "wrong wrong wrong"})
    assert c.post("/api/login", json={"email": "sam@example.com", "password": PASSWORD}).status_code == 200
    for _ in range(4):
        assert c.post("/api/login", json={"email": "sam@example.com", "password": "wrong wrong wrong"}).status_code == 401


def test_logout_ends_the_session_server_side(app, client):
    token = client.cookies.get("sid")
    assert client.post("/api/logout").status_code == 200
    stolen = TestClient(app, headers={CSRF_HEADER: CSRF_VALUE}, cookies={"sid": token})
    assert stolen.get("/api/me").status_code == 401


def test_sessions_expire(app, client, clock):
    clock.advance(7 * 86400 + 1)
    assert client.get("/api/me").status_code == 401


@pytest.mark.parametrize("method, path", [
    ("get", "/api/me"), ("get", "/api/profile"), ("put", "/api/profile"), ("get", "/api/proposals"), ("post", "/api/proposals/generate"),
    ("get", "/api/proposals/1"), ("put", "/api/proposals/1"), ("delete", "/api/proposals/1"), ("get", "/api/stats"),
])
def test_every_private_route_needs_a_login(app, method, path):
    c = TestClient(app, headers={CSRF_HEADER: CSRF_VALUE})
    assert getattr(c, method)(path, **({"json": {}} if method in ("post", "put") else {})).status_code == 401


# --- profile and generating --------------------------------------------------------------------------

def test_the_profile_round_trips_and_is_tidied(client):
    r = client.put("/api/profile", json={**PROFILE, "name": "  Sam Lee ", "skills": ["Python", " ", "n8n"], "proof_points": ["  one  ", ""]})
    assert r.json()["name"] == "Sam Lee" and r.json()["skills"] == ["Python", "n8n"] and r.json()["proof_points"] == ["one"]
    assert client.get("/api/profile").json() == r.json()


@pytest.mark.parametrize("bad", [{"tone": "angry"}, {"proof_points": ["x"] * 9}, {"name": "x" * 81}])
def test_profile_validation(client, bad):
    assert client.put("/api/profile", json={**PROFILE, **bad}).status_code == 422


def test_a_new_account_has_an_empty_profile(client):
    assert client.get("/api/profile").json()["name"] == ""


def test_generating_needs_a_name_first_and_costs_nothing_without_one(client):
    r = generate(client)
    assert r.status_code == 409 and "name" in r.json()["detail"]
    assert client.get("/api/me").json()["usage"]["used"] == 0


def test_a_draft_is_written_saved_and_counted(ready):
    r = generate(ready, title="HubSpot chatbot")
    assert r.status_code == 201
    p = r.json()
    assert p["title"] == "HubSpot chatbot" and p["engine"] == "offline" and p["status"] == "draft" and p["body"].endswith("Sam Lee")
    assert p["usage"] == {"used": 1, "limit": 3, "month": "2026-10"}
    assert ready.get(f"/api/proposals/{p['id']}").json()["job_text"] == JOB


def test_the_title_defaults_to_the_start_of_the_post_cut_at_a_word(ready):
    title = generate(ready).json()["title"]
    assert title == "We need a chatbot for our website that qualifies leads and..." and len(title) <= 63
    short = "Fix my API " + "x" * 40
    assert generate(ready, short + " and a lot more words to reach fifty characters").json()["title"].endswith("...")


@pytest.mark.parametrize("job", ["", "too short", "x" * 6001])
def test_the_job_post_length_is_validated(ready, job):
    assert generate(ready, job).status_code == 422
    assert ready.get("/api/me").json()["usage"]["used"] == 0


def test_the_free_allowance_runs_out_and_comes_back_next_month(ready, clock):
    assert [generate(ready).status_code for _ in range(3)] == [201, 201, 201]
    over = generate(ready)
    assert over.status_code == 402 and "all 3 proposals" in over.json()["detail"]
    clock.advance(31 * 86400)
    assert ready.get("/api/me").status_code == 401  # the session has expired by now, so log in again
    ready.post("/api/login", json={"email": "sam@example.com", "password": PASSWORD})
    assert ready.get("/api/me").json()["usage"] == {"used": 0, "limit": 3, "month": "2026-11"}
    assert generate(ready).status_code == 201


def test_a_failure_while_writing_costs_the_customer_nothing(tmp_path, clock):
    def broken(job, profile):
        raise RuntimeError("boom")
    app = create_app(Settings(db_path=str(tmp_path / "f.db"), monthly_limit=3), writer=broken, mode="offline", clock=clock)
    c = new_client(app)
    c.put("/api/profile", json=PROFILE)
    r = generate(c)
    assert r.status_code == 502 and "Nothing was used" in r.json()["detail"]
    assert c.get("/api/me").json()["usage"]["used"] == 0 and c.get("/api/proposals").json() == []


def test_warnings_are_returned_with_the_draft(ready):
    r = generate(ready, "I need a WordPress plugin fixed for my shop, it has been broken since the last update.")
    assert any("None of your proof points matches" in w for w in r.json()["warnings"])


# --- isolation between customers ----------------------------------------------------------------------

def test_one_customer_cannot_reach_anothers_proposals(app, ready):
    pid = generate(ready).json()["id"]
    other = new_client(app, email="eve@example.com")
    assert other.get(f"/api/proposals/{pid}").status_code == 404
    assert other.put(f"/api/proposals/{pid}", json={"body": "hijacked"}).status_code == 404
    assert other.delete(f"/api/proposals/{pid}").status_code == 404
    assert other.get("/api/proposals").json() == [] and other.get("/api/stats").json()["total"] == 0
    assert ready.get(f"/api/proposals/{pid}").json()["body"].startswith("Hello")


def test_allowances_are_separate_per_customer(app, ready):
    for _ in range(3):
        generate(ready)
    other = new_client(app, email="eve@example.com")
    other.put("/api/profile", json={**PROFILE, "name": "Eve"})
    assert generate(other).status_code == 201


# --- editing, status, stats -----------------------------------------------------------------------------

def test_a_proposal_can_be_edited_and_moved_through_the_pipeline(ready):
    pid = generate(ready).json()["id"]
    r = ready.put(f"/api/proposals/{pid}", json={"body": "My own words.", "status": "sent", "title": "Renamed"})
    assert r.json()["body"] == "My own words." and r.json()["status"] == "sent" and r.json()["title"] == "Renamed"
    assert ready.put(f"/api/proposals/{pid}", json={"status": "won"}).json()["body"] == "My own words."
    assert ready.get("/api/stats").json() == {"draft": 0, "sent": 0, "won": 1, "lost": 0, "total": 1, "win_rate": 1.0}


@pytest.mark.parametrize("bad", [{"status": "pending"}, {"body": ""}, {"title": ""}, {"body": "x" * 6001}])
def test_edit_validation(ready, bad):
    pid = generate(ready).json()["id"]
    assert ready.put(f"/api/proposals/{pid}", json=bad).status_code == 422


def test_the_list_is_newest_first_and_leaves_out_the_long_job_text(ready, clock):
    first = generate(ready, title="first").json()["id"]
    clock.advance(5)
    second = generate(ready, title="second").json()["id"]
    listed = ready.get("/api/proposals").json()
    assert [p["id"] for p in listed] == [second, first] and all(p["job_text"] == "" for p in listed)


def test_deleting_removes_it_but_not_the_month_s_usage(ready):
    pid = generate(ready).json()["id"]
    assert ready.delete(f"/api/proposals/{pid}").status_code == 200
    assert ready.get(f"/api/proposals/{pid}").status_code == 404
    assert ready.get("/api/me").json()["usage"]["used"] == 1  # deleting must not hand the allowance back


def test_unknown_proposals_are_404(ready):
    assert ready.get("/api/proposals/999").status_code == 404 and ready.put("/api/proposals/999", json={"status": "sent"}).status_code == 404


def test_health_and_page(app):
    c = TestClient(app)
    assert c.get("/api/health").json() == {"ok": True, "mode": "offline"}
    assert "Proposal Writer" in c.get("/").text
