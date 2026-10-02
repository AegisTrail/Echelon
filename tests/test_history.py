from history import default_db_path, list_changes, record_change


def test_record_and_list(tmp_path):
    db = str(tmp_path / "h.db")
    rid = record_change(
        db,
        snippet_id="s1",
        file_url="https://github.com/o/r/blob/main/f.py#L1-L2",
        old_code="a\n",
        new_code="b\n",
        diff="-a\n+b\n",
        summary="changed",
        commit_sha="abc123",
        commit_url="https://example.com",
    )
    assert rid == 1
    rows = list_changes(db, limit=10)
    assert len(rows) == 1
    assert rows[0].diff == "-a\n+b\n" and rows[0].commit_sha == "abc123"


def test_list_missing_db_returns_empty(tmp_path):
    assert list_changes(str(tmp_path / "nope.db")) == []


def test_default_db_path_override(tmp_path):
    cfg = str(tmp_path / "config.json")
    assert default_db_path(cfg, "").endswith("echelon-history.db")
    assert default_db_path(cfg, "custom.db").endswith("custom.db")
