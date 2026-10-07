import threading
from datetime import datetime, timezone

import pytest

from orquehelx import pauses

RESET = datetime(2026, 9, 26, 19, 0, tzinfo=timezone.utc)


@pytest.fixture
def con(tmp_path):
    c = pauses.open_db(tmp_path / "pauses.db")
    yield c
    c.close()


def test_create_and_list_the_pending_pause(con):
    p = pauses.create(con, "session-1", "claude-subscription-directsdk-experimental", "claude-haiku-4-5", RESET)
    assert p["state"] == "paused"
    assert p["reset_at"] == RESET.isoformat()
    assert pauses.pending(con) == [p]


def test_a_session_has_a_single_pending_pause(con):
    a = pauses.create(con, "session-1", "claude", None, RESET)
    b = pauses.create(con, "session-1", "claude", None, None)
    assert a == b
    assert len(pauses.pending(con)) == 1


def test_resolving_clears_the_pause_and_records_the_action(con):
    p = pauses.create(con, "session-1", "claude", None, None)
    assert pauses.resolve(con, p["id"], "resend", "agy") is True
    assert pauses.pending(con) == []
    row = con.execute("SELECT state, target, resolved FROM pauses WHERE id = ?", (p["id"],)).fetchone()
    assert row[0] == "resent" and row[1] == "agy" and row[2] is not None


def test_resolving_twice_only_the_first_wins(con):
    p = pauses.create(con, "session-1", "claude", None, None)
    assert pauses.resolve(con, p["id"], "cancel") is True
    assert pauses.resolve(con, p["id"], "resume") is False
    assert con.execute("SELECT state FROM pauses").fetchone()[0] == "cancelled"


def test_unknown_action_fails_loudly(con):
    p = pauses.create(con, "session-1", "claude", None, None)
    with pytest.raises(ValueError):
        pauses.resolve(con, p["id"], "delete")


def test_race_between_connections_resolves_only_one(tmp_path):
    # Resuming at the reset and a click in another tab at once: only one spends the turn.
    path = tmp_path / "pauses.db"
    with pauses.open_db(path) as c:
        pid = pauses.create(c, "session-1", "claude", None, None)["id"]
    winners: list[bool] = []
    barrier = threading.Barrier(8)

    def attempt():
        c = pauses.open_db(path)
        barrier.wait()
        winners.append(pauses.resolve(c, pid, "resume"))
        c.close()

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert sorted(winners) == [False] * 7 + [True]


def test_a_resolved_session_can_pause_again(con):
    p = pauses.create(con, "session-1", "claude", None, None)
    pauses.resolve(con, p["id"], "resume")
    q = pauses.create(con, "session-1", "claude", None, None)
    assert q["id"] != p["id"] and q["state"] == "paused"


def _v01_db(path):
    import sqlite3
    with sqlite3.connect(path) as c:
        c.executescript(
            "CREATE TABLE pausas (id INTEGER PRIMARY KEY, sesion TEXT NOT NULL, proveedor TEXT NOT NULL, modelo TEXT,"
            " reinicio TEXT, estado TEXT NOT NULL DEFAULT 'pausado', destino TEXT, creado REAL NOT NULL, resuelto REAL);"
            "INSERT INTO pausas (sesion, proveedor, creado) VALUES ('s1', 'claude', 1);")
    c.close()


def test_a_failed_v01_copy_is_retried_on_the_next_open(tmp_path):
    legacy = tmp_path / "pausas.db"
    legacy.write_bytes(b"not a database" * 100)
    with pytest.raises(pauses.sqlite3.DatabaseError):
        pauses.open_db(tmp_path / "pauses.db", legacy=legacy)
    legacy.unlink()
    _v01_db(legacy)
    c = pauses.open_db(tmp_path / "pauses.db", legacy=legacy)
    assert [p["session"] for p in pauses.pending(c)] == ["s1"]
    c.close()


def test_the_v01_copy_happens_once(tmp_path):
    legacy = tmp_path / "pausas.db"
    _v01_db(legacy)
    for _ in range(2):  # a second open (or a concurrent first one) must not copy again
        c = pauses.open_db(tmp_path / "pauses.db", legacy=legacy)
        c.close()
    c = pauses.open_db(tmp_path / "pauses.db", legacy=legacy)
    assert len(pauses.pending(c)) == 1
    c.close()


def test_an_integrity_error_that_is_not_a_duplicate_is_not_swallowed(con):
    with pytest.raises(pauses.sqlite3.IntegrityError):
        pauses.create(con, "session-1", None, None, None)
