"""Line-drift resistance: anchors + fuzzy block re-alignment.

Problem: monitoring absolute L26-L31 breaks when lines are inserted above.
Solution (two layers):
1. Anchor mode: if the snippet has ``anchor`` (literal) or ``anchor_regex``,
   locate the anchor in the fresh file and derive the window from it.
2. Auto-realign: otherwise, fuzzy-search the last-seen block in the fresh
   file. If the identical (or near-identical) block moved, update line numbers
   silently instead of alerting.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass


@dataclass
class ResolveResult:
    start_line: int
    end_line: int
    code: str
    drifted: bool  # line numbers moved
    method: str  # "exact" | "anchor" | "anchor-regex" | "fuzzy" | "fallback"


def _find_anchor_line(lines: list[str], anchor: str, anchor_regex: str, hint: int) -> int | None:
    """Return 1-based line number of best anchor match, or None."""
    candidates: list[int] = []
    if anchor:
        needle = anchor.strip()
        if needle:
            for i, line in enumerate(lines, start=1):
                if needle in line:
                    candidates.append(i)
    elif anchor_regex:
        try:
            pattern = re.compile(anchor_regex)
        except re.error:
            return None
        for i, line in enumerate(lines, start=1):
            if pattern.search(line):
                candidates.append(i)
    else:
        return None
    if not candidates:
        return None
    # Closest to the previous location wins (stable under duplicates).
    return min(candidates, key=lambda ln: (abs(ln - hint), ln))


def _best_fuzzy_window(
    lines: list[str], snippet_lines: list[str], *, min_ratio: float = 0.85
) -> tuple[int, float] | None:
    """Slide the snippet window over *lines*; return (start_1based, ratio)."""
    n = len(snippet_lines)
    if n == 0 or len(lines) < n:
        return None
    # Fast path: exact contiguous match via difflib.
    text = "\n".join(snippet_lines)
    best: tuple[int, float] | None = None
    # Limit work: compare at most ~4000 windows (large files).
    step = max(1, (len(lines) - n + 1) // 4000 + 1)
    matcher = difflib.SequenceMatcher(None, text, "")
    for start in range(0, len(lines) - n + 1, step):
        window = "\n".join(lines[start : start + n])
        if window == text:
            return (start + 1, 1.0)
        matcher.set_seq2(window)
        ratio = matcher.ratio()
        if best is None or ratio > best[1]:
            best = (start + 1, ratio)
    if best and best[1] >= min_ratio:
        return best
    return None


def resolve_snippet(
    content: str,
    prev_start: int,
    prev_end: int,
    last_seen_code: str,
    *,
    anchor: str = "",
    anchor_regex: str = "",
    context_lines: int = 3,  # reserved for future context display; validated 0..20
) -> ResolveResult:
    """Map the previously monitored range onto fresh *content*.

    Returns the code at the resolved location. Callers compare
    ``result.code`` with ``last_seen_code`` to decide if content changed;
    ``drifted`` tells them line numbers moved.
    """
    _ = context_lines  # currently informational; kept for schema compat
    lines = content.splitlines()
    total = len(lines)
    length = max(1, prev_end - prev_start + 1)

    def slice_at(start: int) -> str:
        end = min(total, start + length - 1)
        chunk = lines[start - 1 : end]
        return "\n".join(chunk) + ("\n" if chunk else "")

    # 1. Anchor mode.
    if anchor or anchor_regex:
        found = _find_anchor_line(lines, anchor or "", anchor_regex or "", hint=prev_start)
        if found is not None:
            start = max(1, found)
            end = min(total, start + length - 1)
            code = slice_at(start)
            method = "anchor-regex" if anchor_regex else "anchor"
            return ResolveResult(start, end, code, drifted=(start != prev_start), method=method)
        # Anchor vanished: fall through to direct slice (will likely alert,
        # which is correct: the tracked function may have been deleted).

    # 2. Direct slice first (common no-change case, cheapest).
    if 1 <= prev_start <= total:
        direct = slice_at(prev_start)
        if direct == last_seen_code:
            return ResolveResult(
                prev_start, min(total, prev_start + length - 1), direct, False, "exact"
            )

    # 3. Fuzzy re-align: block moved but content identical/similar.
    snippet_lines = last_seen_code.splitlines()
    if snippet_lines:
        found = _best_fuzzy_window(lines, snippet_lines)
        if found is not None:
            start, ratio = found
            _ = ratio
            code = slice_at(start)
            if code == last_seen_code:
                return ResolveResult(
                    start, start + length - 1, code, drifted=(start != prev_start), method="fuzzy"
                )

    # 4. Fallback: same absolute lines (content probably really changed).
    start = prev_start
    end = min(total, prev_start + length - 1) if total else prev_end
    code = slice_at(start) if total else ""
    return ResolveResult(start, end, code, drifted=False, method="fallback")
