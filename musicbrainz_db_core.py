"""
Same interface as musicbrainz_core.py, but reading from a local, pre-trimmed
SQLite copy of MusicBrainz instead of hitting the live web API.

The SQLite file (data/musicbrainz_classical.sqlite) holds only recordings
that are linked to a work (the classical/jazz-shaped slice of MusicBrainz),
their artist credits and first-release year, each work's composer, and the
work-to-work "parts" (movement) structure. See data/README.md for how it
was built and how to rebuild it.

Because this is a static local file there's no rate limit to respect and no
network round-trips, so fetch_all_recordings here is dramatically faster than
the live-API version — but it's only as fresh as the dump it was built from.
"""

from __future__ import annotations

import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "musicbrainz_classical.sqlite"

_MBID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)

# Floor below which a "recording" is junk data, not a real performance (e.g.
# MusicBrainz entries a few ms long). Also guards against NULL implicitly
# (any comparison against NULL is false in SQL) so this one condition
# replaces a separate "IS NOT NULL" check.
MIN_RECORDING_LENGTH_MS = 1000

# Search results pooling fewer recordings than this across all of a work's
# movements are filtered out of search entirely — see search_work.
DEFAULT_MIN_RECORDING_COUNT = 50


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _format_title(name: str, composer_name: str | None) -> str:
    return f"{composer_name}: {name}" if composer_name else name


_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def _dedup_key(title: str) -> str:
    """
    Normalizes a title for duplicate-detection only (never displayed):
    lowercased, punctuation stripped, whitespace collapsed. Catches
    MusicBrainz duplicate work entries that differ only in punctuation
    (e.g. "III. Allegro, ma non tanto" vs "III. Allegro. Ma non tanto").
    """
    stripped = _PUNCT_RE.sub(" ", title.lower())
    return re.sub(r"\s+", " ", stripped).strip()


def _fts_query(query: str) -> str:
    """
    Turns free text into an FTS5 query that ANDs together each token as a
    phrase literal, so punctuation/special characters in user input can't
    break the MATCH syntax.
    """
    tokens = [t for t in re.split(r"\s+", query.strip()) if t]
    escaped = [f'"{t.replace(chr(34), chr(34) * 2)}"' for t in tokens]
    return " ".join(escaped)


def search_work(
    query: str,
    limit: int = 10,
    conn: sqlite3.Connection | None = None,
    min_recording_count: int = DEFAULT_MIN_RECORDING_COUNT,
) -> list[dict]:
    """
    Full-text search over work names and composer names (no alias index —
    see data/README.md for that limitation). Returns
    [{"id": mbid, "title", "score", "recording_count"}, ...], ranked by
    recording_count descending (bm25 text-relevance score as tiebreak —
    see below), truncated to `limit`. "title" is composer-prefixed
    ("Johann Sebastian Bach: Concerto...") when a composer is known.
    "recording_count" is the total pooled recording count across every
    movement of the work's resolved parent (and its duplicate entries) —
    i.e. exactly what you'd get by clicking through to
    fetch_all_recordings(id).

    Candidates with recording_count < min_recording_count are dropped
    entirely, not just ranked lower: a work with only a handful of linked
    recordings is usually a stub, cadenza, or arrangement that happens to
    text-match well rather than something worth surfacing (see
    data/README.md for a concrete example this fixes).

    Only top-level works are returned — a work that's itself a movement of
    a larger work (has a parent via the "parts" relationship) is dropped,
    since clicking it would resolve to and show the exact same pooled chart
    as its parent anyway. Without this, searching a popular piece ("Beethoven
    Symphony 5") used to flood the results list with one row per movement
    of the same work, all carrying an identical recording_count, crowding
    out genuinely different matches.

    Ranking by recording_count rather than bm25 relevance is a deliberate
    choice: within a query specific enough to narrow the FTS candidate pool
    down this far, which of several text-plausible matches is the REAL,
    well-documented work matters more than which one bm25 happened to score
    fractionally higher.

    MusicBrainz's catalog has unmerged duplicate work entries (the same
    piece, entered separately by different editors, never merged, sometimes
    differing only in punctuation) — results are deduplicated by a
    punctuation-insensitive title match, keeping the best-ranked MBID per
    title. This can hide a genuinely distinct work that happens to share an
    identical title+composer string, but that's rare enough to be the right
    tradeoff for a cleaner results list (see data/README.md).
    """
    own_conn = conn is None
    conn = conn or _connect()
    try:
        fts = _fts_query(query)
        if not fts:
            return []
        # Over-fetch: most candidates get dropped by the recording_count
        # floor below, so a shallow pool would too often come up short.
        rows = conn.execute(
            """
            SELECT w.gid AS id, w.name AS name, w.composer_name AS composer_name, bm25(work_fts) AS rank
            FROM work_fts
            JOIN work w ON w.id = work_fts.rowid
            WHERE work_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (fts, max(limit * 15, 150)),
        ).fetchall()

        candidates = []
        seen_keys = set()
        for r in rows:
            title = _format_title(r["name"], r["composer_name"])
            key = _dedup_key(title)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            # Only show top-level works — one that's itself a movement/part
            # of a larger work is redundant with its parent (and clicking
            # either loads the identical pooled chart anyway, via
            # find_parent_work). find_parent_work resolving to something
            # other than r["id"] itself is exactly how "this is a part of a
            # larger work" shows up.
            parent = find_parent_work(r["id"], conn=conn)
            if parent["id"] != r["id"]:
                continue

            count = _total_recording_count(parent, conn=conn)
            if count < min_recording_count:
                continue
            candidates.append({"id": r["id"], "title": title, "score": -r["rank"], "recording_count": count})

        candidates.sort(key=lambda c: (-c["recording_count"], -c["score"]))
        return candidates[:limit]
    finally:
        if own_conn:
            conn.close()


def find_parent_work(work_mbid: str, conn: sqlite3.Connection | None = None) -> dict:
    """
    Walks "parts" relationships upward until reaching a work with no parent,
    returning {"id": mbid, "title"}. "title" is composer-prefixed when a
    composer is known. If work_mbid has no parent, it's already top-level.
    """
    own_conn = conn is None
    conn = conn or _connect()
    try:
        row = conn.execute(
            "SELECT id, name, composer_name FROM work WHERE gid = ?", (work_mbid,)
        ).fetchone()
        if row is None:
            return {"id": work_mbid, "title": work_mbid}

        current_id, current_name, current_composer = row["id"], row["name"], row["composer_name"]
        seen = set()
        while current_id not in seen:
            seen.add(current_id)
            parent = conn.execute(
                "SELECT parent_work_id FROM work_parts WHERE child_work_id = ? LIMIT 1",
                (current_id,),
            ).fetchone()
            if parent is None:
                gid = conn.execute("SELECT gid FROM work WHERE id = ?", (current_id,)).fetchone()["gid"]
                return {"id": gid, "title": _format_title(current_name, current_composer)}

            current_id = parent["parent_work_id"]
            current = conn.execute(
                "SELECT name, composer_name FROM work WHERE id = ?", (current_id,)
            ).fetchone()
            current_name, current_composer = current["name"], current["composer_name"]

        gid = conn.execute("SELECT gid FROM work WHERE id = ?", (current_id,)).fetchone()["gid"]
        return {"id": gid, "title": _format_title(current_name, current_composer)}
    finally:
        if own_conn:
            conn.close()


def _work_parts_for(parent_internal_id: int, conn: sqlite3.Connection) -> list[dict]:
    """
    Raw "parts" children for one work, by internal integer id — no
    single-movement fallback (see fetch_movements / fetch_all_recordings,
    which each need different fallback behavior).
    """
    rows = conn.execute(
        """
        SELECT wp.link_order AS "order", c.gid AS id, c.name AS title
        FROM work_parts wp
        JOIN work c ON c.id = wp.child_work_id
        WHERE wp.parent_work_id = ?
        ORDER BY wp.link_order
        """,
        (parent_internal_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def fetch_movements(parent_work_mbid: str, conn: sqlite3.Connection | None = None) -> list[dict]:
    """
    Returns ordered child movements for a parent work:
    [{"order": int, "id": mbid, "title": str}, ...]
    Falls back to treating the work itself as a single "movement" if it has
    no parts (e.g. a standalone piece).
    """
    own_conn = conn is None
    conn = conn or _connect()
    try:
        parent = conn.execute("SELECT id, name FROM work WHERE gid = ?", (parent_work_mbid,)).fetchone()
        if parent is None:
            return []

        rows = _work_parts_for(parent["id"], conn)
        if not rows:
            return [{"order": 0, "id": parent_work_mbid, "title": parent["name"]}]
        return rows
    finally:
        if own_conn:
            conn.close()


def _find_sibling_works(parent: dict, conn: sqlite3.Connection) -> list[dict]:
    """
    Finds MusicBrainz work entries that normalize to the same title as
    `parent` — i.e. unmerged duplicate entries for the same piece (see
    search_work's docstring). Uses the FTS index to narrow candidates
    (phrase match on the plain work name), then confirms each with an exact
    punctuation-insensitive title comparison, so the FTS containment match
    (which would also surface movement-level works whose name happens to
    start with the same text) can't produce a false positive.

    Returns [{"id": int, "gid": mbid, "title": formatted}, ...], always
    including `parent` itself, deduplicated by internal id.
    """
    target_key = _dedup_key(parent["title"])
    seed = conn.execute("SELECT id, name FROM work WHERE gid = ?", (parent["id"],)).fetchone()

    siblings: dict[int, dict] = {}
    if seed is not None:
        siblings[seed["id"]] = {"id": seed["id"], "gid": parent["id"], "title": parent["title"]}

        escaped = seed["name"].replace('"', '""')
        candidates = conn.execute(
            """
            SELECT w.id, w.gid, w.name, w.composer_name
            FROM work_fts
            JOIN work w ON w.id = work_fts.rowid
            WHERE work_fts MATCH ?
            """,
            (f'name:"{escaped}"',),
        ).fetchall()

        for c in candidates:
            title = _format_title(c["name"], c["composer_name"])
            if _dedup_key(title) == target_key:
                siblings[c["id"]] = {"id": c["id"], "gid": c["gid"], "title": title}

    return list(siblings.values())


def _pooled_parts_by_order(parent: dict, conn: sqlite3.Connection) -> dict[int, list[dict]]:
    """
    Shared by fetch_all_recordings and _total_recording_count: finds
    duplicate entries for `parent` (see _find_sibling_works) and groups
    their movement parts by order, with the same no-movement-breakdown
    fallback fetch_all_recordings documents.
    """
    siblings = _find_sibling_works(parent, conn)

    all_parts = []
    for sib in siblings:
        all_parts.extend(_work_parts_for(sib["id"], conn))
    if not all_parts:
        all_parts = [{"order": 0, "id": sib["gid"], "title": sib["title"]} for sib in siblings]

    by_order: dict[int, list[dict]] = defaultdict(list)
    for part in all_parts:
        by_order[part["order"]].append(part)
    return by_order


def _total_recording_count(parent: dict, conn: sqlite3.Connection) -> int:
    """
    Total distinct recordings across every movement of `parent`, pooled
    across its duplicate entries — the same count fetch_all_recordings(parent)
    would produce, computed directly in SQL instead of building full
    per-recording detail.
    """
    by_order = _pooled_parts_by_order(parent, conn)
    movement_gids = [m["id"] for group in by_order.values() for m in group]
    if not movement_gids:
        return 0

    placeholders = ",".join("?" for _ in movement_gids)
    row = conn.execute(
        f"""
        SELECT COUNT(DISTINCT r.gid)
        FROM recording_work rw
        JOIN work w ON w.id = rw.work_id
        JOIN recording r ON r.id = rw.recording_id
        WHERE w.gid IN ({placeholders}) AND r.length >= {MIN_RECORDING_LENGTH_MS}
        """,
        movement_gids,
    ).fetchone()
    return row[0] if row else 0


def fetch_recordings_for_movement(movement_work_mbid: str, conn: sqlite3.Connection | None = None) -> list[dict]:
    """
    Returns all recordings of a single movement-work:
    [{"id": mbid, "ms": int, "performer": str, "year": int | None}, ...]
    Recordings with no duration, or a junk sub-MIN_RECORDING_LENGTH_MS
    duration (a handful of ms, not a real performance), are excluded.
    "year" (first release year) is missing for ~6% of recordings — see
    data/README.md.
    """
    own_conn = conn is None
    conn = conn or _connect()
    try:
        work = conn.execute("SELECT id FROM work WHERE gid = ?", (movement_work_mbid,)).fetchone()
        if work is None:
            return []

        rows = conn.execute(
            f"""
            SELECT r.gid AS id, r.length AS ms, ac.name AS performer, r.year AS year
            FROM recording_work rw
            JOIN recording r ON r.id = rw.recording_id
            JOIN artist_credit ac ON ac.id = r.artist_credit
            WHERE rw.work_id = ? AND r.length >= {MIN_RECORDING_LENGTH_MS}
            """,
            (work["id"],),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if own_conn:
            conn.close()


def fetch_all_recordings(work_id_or_query: str, on_movement=None) -> dict:
    """
    End-to-end: resolve a work (by MBID or search query) to its parent work,
    find any unmerged duplicate entries for the same piece (see
    _find_sibling_works), and pool their movements and recordings together
    so duplicates in the catalog don't silently hide recordings from the
    chart. on_movement: optional callback(movement_title, count) called
    after each movement is fetched.

    Movements are aligned across duplicates by order (link_order), not by
    title text, since duplicate entries sometimes word a movement's title
    slightly differently; the most common title text among the group is
    used for display. Recordings are deduplicated by MBID when pooling, in
    case the same actual recording is somehow linked from more than one
    duplicate work.

    If none of the duplicates have a movement breakdown of their own (a
    standalone, non-multi-movement piece), each duplicate's own recordings
    are pooled into a single order-0 bucket instead — a sparse duplicate
    entry with no movements is never blended into what would otherwise be
    "movement I" of a properly-decomposed duplicate, which would corrupt
    that movement's duration data with whole-piece recording lengths.

    Returns the same shape as musicbrainz_core.fetch_all_recordings:
        {"{order}||{title}": [{"ms", "id", "performer", "year"}, ...]}
    """
    conn = _connect()
    try:
        if _MBID_RE.match(work_id_or_query):
            seed_mbid = work_id_or_query
        else:
            candidates = search_work(work_id_or_query, limit=1, conn=conn)
            if not candidates:
                return {}
            seed_mbid = candidates[0]["id"]

        parent = find_parent_work(seed_mbid, conn=conn)
        by_order = _pooled_parts_by_order(parent, conn=conn)

        out: dict[str, list[dict]] = {}
        for order in sorted(by_order):
            group = by_order[order]
            display_title = Counter(m["title"] for m in group).most_common(1)[0][0]

            seen_recording_ids: set[str] = set()
            pooled: list[dict] = []
            for m in group:
                for rec in fetch_recordings_for_movement(m["id"], conn=conn):
                    if rec["id"] in seen_recording_ids:
                        continue
                    seen_recording_ids.add(rec["id"])
                    pooled.append(rec)

            if on_movement:
                on_movement(display_title, len(pooled))
            out[f"{order}||{display_title}"] = pooled

        out["_title"] = parent["title"]
        return out
    finally:
        conn.close()
