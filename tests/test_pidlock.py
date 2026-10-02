"""PID lock: exclusivity, stale takeover, self-ownership on release."""

import os

import pytest

from pidlock import LockHeldError, PidLock, default_lock_path


def test_acquire_and_release(tmp_path):
    lock = PidLock(str(tmp_path / "echelon.lock"))
    lock.acquire()
    assert os.path.exists(str(tmp_path / "echelon.lock"))
    lock.release()
    assert not os.path.exists(str(tmp_path / "echelon.lock"))


def test_second_acquire_refused_while_held(tmp_path):
    first = PidLock(str(tmp_path / "echelon.lock"))
    first.acquire()
    try:
        with pytest.raises(LockHeldError):
            PidLock(str(tmp_path / "echelon.lock")).acquire()
    finally:
        first.release()


def test_context_manager_releases(tmp_path):
    path = str(tmp_path / "echelon.lock")
    with PidLock(path):
        assert os.path.exists(path)
    assert not os.path.exists(path)


def test_stale_lock_taken_over(tmp_path):
    path = str(tmp_path / "echelon.lock")
    with open(path, "w") as fh:
        fh.write("99999999")  # no such process: definitely stale
    PidLock(path).acquire()  # must not raise
    with open(path) as fh:
        assert fh.read().strip() == str(os.getpid())


def test_release_never_removes_foreign_lock(tmp_path):
    path = str(tmp_path / "echelon.lock")
    with open(path, "w") as fh:
        fh.write("99999999")
    lock = PidLock(path)
    lock.release()  # never acquired: must not delete
    assert os.path.exists(path)


def test_default_lock_path_next_to_config(tmp_path):
    cfg = str(tmp_path / "sub" / "config.json")
    assert default_lock_path(cfg) == str(tmp_path / "sub" / "echelon.lock")
