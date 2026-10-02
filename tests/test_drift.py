from drift import resolve_snippet


def test_exact_no_drift():
    content = "a\nb\nc\nd\ne\n"
    last = "b\nc\n"
    r = resolve_snippet(content, 2, 3, last)
    assert r.code == last and r.drifted is False and r.method == "exact"


def test_block_moved_realigns_silently():
    # "x" inserted at top; block b/c moves 2-3 -> 3-4 with same content.
    content = "x\na\nb\nc\nd\n"
    last = "b\nc\n"
    r = resolve_snippet(content, 2, 3, last)
    assert r.code == last
    assert (r.start_line, r.end_line) == (3, 4)
    assert r.drifted is True and r.method == "fuzzy"


def test_anchor_tracks_function():
    content = "header\ndef target():\n    body1\n    body2\nfooter\n"
    last = "def target():\n    body1\n"
    r = resolve_snippet(content, 2, 3, last, anchor="def target")
    assert r.code == last and r.start_line == 2


def test_anchor_follows_after_insert():
    content = "new line\nheader\ndef target():\n    body1\n    body2\n"
    last = "def target():\n    body1\n"
    r = resolve_snippet(content, 2, 3, last, anchor="def target")
    assert r.start_line == 3 and r.code == last and r.drifted is True


def test_anchor_regex():
    content = "a\nmint(amount):\nb\n"
    r = resolve_snippet(content, 2, 2, "mint(amount):\n", anchor_regex=r"mint\s*\(")
    assert r.start_line == 2 and r.method == "anchor-regex"


def test_real_change_detected():
    content = "a\nB\nc\nd\n"
    last = "b\nc\n"
    r = resolve_snippet(content, 2, 3, last)
    assert r.code != last  # caller will alert
