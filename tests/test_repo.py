import threading

import pytest

from app.repo import Repo, month_key
from tests.conftest import START, Clock


@pytest.fixture
def repo(tmp_path):
    clock = Clock()
    r = Repo(tmp_path / "r.db", clock)
    r.clock_ref = clock
    return r


def two_users(repo):
    return repo.create_user("a@x.co", "h"), repo.create_user("b@x.co", "h")


def test_an_email_can_only_register_once(repo):
    assert repo.create_user("a@x.co", "h") and repo.create_user("a@x.co", "h") is None


def test_one_user_cannot_see_change_or_delete_anothers_proposal(repo):
    a, b = two_users(repo)
    pid = repo.add_proposal(a, "Mine", "job", "body", "offline", [])
    assert repo.proposal(b, pid) is None and repo.proposals(b) == []
    assert not repo.update_proposal(b, pid, body="hacked") and not repo.delete_proposal(b, pid)
    assert repo.proposal(a, pid)["body"] == "body"


def test_profiles_and_stats_are_per_user(repo):
    a, b = two_users(repo)
    repo.set_profile(a, {"name": "Ann"})
    repo.add_proposal(a, "t", "j", "b", "offline", [])
    assert repo.get_profile(b)["name"] == "" and repo.stats(b)["total"] == 0 and repo.stats(a)["total"] == 1


def test_only_the_editable_fields_can_be_changed(repo):
    a, b = two_users(repo)
    pid = repo.add_proposal(a, "t", "j", "b", "offline", [])
    repo.update_proposal(a, pid, status="sent", engine="claude", job_text="changed")
    p = repo.proposal(a, pid)
    assert p["status"] == "sent" and p["engine"] == "offline" and p["job_text"] == "j" and repo.proposal(b, pid) is None


def test_updating_nothing_still_reports_whether_the_proposal_exists(repo):
    a, b = two_users(repo)
    pid = repo.add_proposal(a, "t", "j", "b", "offline", [])
    assert repo.update_proposal(a, pid) and not repo.update_proposal(b, pid)


def test_the_monthly_allowance_stops_at_the_limit_and_resets_next_month(repo):
    a, _ = two_users(repo)
    assert [repo.try_consume(a, 2) for _ in range(3)] == [True, True, False]
    assert repo.usage(a) == 2
    repo.clock_ref.advance(31 * 86400)
    assert month_key(repo.clock()) != month_key(START) and repo.usage(a) == 0 and repo.try_consume(a, 2)


def test_ten_requests_at_once_cannot_take_more_than_the_limit(repo):
    a, _ = two_users(repo)
    results = []
    threads = [threading.Thread(target=lambda: results.append(repo.try_consume(a, 5))) for _ in range(10)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert sum(results) == 5 and repo.usage(a) == 5


def test_a_refund_gives_the_slot_back_but_never_goes_below_zero(repo):
    a, _ = two_users(repo)
    repo.try_consume(a, 3); repo.refund(a); repo.refund(a)
    assert repo.usage(a) == 0


def test_sessions_expire(repo):
    a, _ = two_users(repo)
    repo.create_session(a, "tok", 100)
    assert repo.user_for_token("tok")["id"] == a
    repo.clock_ref.advance(101)
    assert repo.user_for_token("tok") is None and repo.user_for_token("other") is None


def test_login_failures_are_counted_inside_a_window_and_can_be_cleared(repo):
    for _ in range(3):
        repo.record_failure("a@x.co")
    assert repo.failures("a@x.co", 900) == 3 and repo.failures("b@x.co", 900) == 0
    repo.clock_ref.advance(901)
    assert repo.failures("a@x.co", 900) == 0
    repo.record_failure("a@x.co"); repo.clear_failures("a@x.co")
    assert repo.failures("a@x.co", 900) == 0


def test_win_rate_counts_only_decided_proposals(repo):
    a, _ = two_users(repo)
    assert repo.stats(a)["win_rate"] is None
    ids = [repo.add_proposal(a, "t", "j", "b", "offline", []) for _ in range(5)]
    for pid, status in zip(ids, ["won", "won", "lost", "sent", "draft"]):
        repo.update_proposal(a, pid, status=status)
    s = repo.stats(a)
    assert (s["total"], s["won"], s["lost"], s["sent"], s["draft"], s["win_rate"]) == (5, 2, 1, 1, 1, 0.67)
