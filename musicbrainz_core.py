"""
Prototype: fetch work/movement/recording duration data from MusicBrainz instead
of Apple Music Classical.

Unlike core.py (which clusters tracks into movements via fuzzy title matching,
because Apple's API has no formal movement concept), MusicBrainz models
movements explicitly: a parent "work" is linked to child "works" (one per
movement) via a work-work relationship of type "parts", ordered by
`ordering-key`. So instead of clustering, we just walk that relationship graph.

Output shape matches core.py's `_cluster_tracks` / `fetch_all_recordings`:
    {"{order}||{movement title}": [{"ms": int, "id": str, "performer": str}, ...]}
so this can be compared directly against the Apple-sourced data, or swapped
into app.py / plot_work.py later.

MusicBrainz API etiquette requires a descriptive User-Agent with contact info,
and enforces ~1 request/second per IP for the public API (no key required).
We serialize all requests through a single rate limiter to respect that.
"""

import threading
import time

import requests

API_ROOT = "https://musicbrainz.org/ws/2"
USER_AGENT = "AppleMusicClassicalExplorer/0.1 (swm.mayhew@gmail.com)"
MIN_REQUEST_INTERVAL_S = 1.1  # MusicBrainz asks for ~1 req/sec per client

_rate_lock = threading.Lock()
_last_request_at = 0.0


def _get(path: str, params: dict) -> dict:
    """
    GET against the MusicBrainz API, serialized to respect the rate limit.
    Retries once on 503 (service unavailable / rate limited).
    """
    global _last_request_at

    with _rate_lock:
        wait = MIN_REQUEST_INTERVAL_S - (time.monotonic() - _last_request_at)
        if wait > 0:
            time.sleep(wait)

        params = {**params, "fmt": "json"}
        headers = {"User-Agent": USER_AGENT}
        url = f"{API_ROOT}/{path}"

        for attempt in range(2):
            resp = requests.get(url, params=params, headers=headers, timeout=15)
            _last_request_at = time.monotonic()
            if resp.status_code == 503:
                time.sleep(1.5)
                continue
            resp.raise_for_status()
            return resp.json()

        resp.raise_for_status()
        return resp.json()


def search_work(query: str, limit: int = 10) -> list[dict]:
    """
    Full-text search for works. Returns [{"id", "title", "score"}, ...],
    best match first. Note this often matches at movement granularity
    (e.g. "...: II. Largo ma non tanto") rather than the parent work —
    use find_parent_work() to walk up to the top-level work.
    """
    data = _get("work", {"query": query, "limit": limit})
    return [
        {"id": w["id"], "title": w.get("title", ""), "score": w.get("score", 0)}
        for w in data.get("works", [])
    ]


def find_parent_work(work_id: str) -> dict:
    """
    Walks "parts" relationships upward until reaching a work with no parent,
    returning {"id", "title"} of the top-level work. If work_id has no
    parent, it's already top-level.
    """
    current_id = work_id
    seen = set()
    while current_id not in seen:
        seen.add(current_id)
        data = _get(f"work/{current_id}", {"inc": "work-rels"})
        parent = None
        for rel in data.get("relations", []):
            if rel.get("type") == "parts" and rel.get("direction") == "backward":
                parent = rel["work"]
                break
        if parent is None:
            return {"id": current_id, "title": data.get("title", current_id)}
        current_id = parent["id"]

    return {"id": current_id, "title": data.get("title", current_id)}


def fetch_movements(parent_work_id: str) -> list[dict]:
    """
    Returns ordered child movements for a parent work:
    [{"order": int, "id": str, "title": str}, ...]
    Falls back to treating the work itself as a single "movement" if it has
    no parts (e.g. a standalone piece).
    """
    data = _get(f"work/{parent_work_id}", {"inc": "work-rels"})
    parts = [
        rel for rel in data.get("relations", [])
        if rel.get("type") == "parts" and rel.get("direction") == "forward"
    ]
    if not parts:
        return [{"order": 0, "id": parent_work_id, "title": data.get("title", parent_work_id)}]

    movements = [
        {
            "order": rel.get("ordering-key", i),
            "id": rel["work"]["id"],
            "title": rel["work"].get("title", rel["work"]["id"]),
        }
        for i, rel in enumerate(parts)
    ]
    movements.sort(key=lambda m: m["order"])
    return movements


def _format_performer(artist_credit: list[dict]) -> str:
    parts = []
    for ac in artist_credit or []:
        parts.append(ac.get("name", ""))
        parts.append(ac.get("joinphrase", ""))
    return "".join(parts).strip()


def fetch_recordings_for_movement(movement_work_id: str, limit: int = 100) -> list[dict]:
    """
    Returns all recordings of a single movement-work:
    [{"id": str, "ms": int, "performer": str}, ...]
    Skips recordings with no duration (MusicBrainz data is community-entered
    and often incomplete).
    """
    results = []
    offset = 0
    while True:
        data = _get(
            "recording",
            {
                "work": movement_work_id,
                "inc": "artist-credits",
                "limit": limit,
                "offset": offset,
            },
        )
        recordings = data.get("recordings", [])
        if not recordings:
            break

        for r in recordings:
            length_ms = r.get("length")
            if not length_ms:
                continue
            results.append(
                {
                    "id": r["id"],
                    "ms": int(length_ms),
                    "performer": _format_performer(r.get("artist-credit")),
                }
            )

        offset += len(recordings)
        if offset >= data.get("recording-count", 0):
            break

    return results


def fetch_all_recordings(work_id_or_query: str, on_movement=None) -> dict:
    """
    End-to-end: resolve a work (by MBID or search query) to its parent work,
    walk its movements, and fetch recording durations for each.
    on_movement: optional callback(movement_title, count) called after each
    movement is fetched.

    Returns the same shape as core.fetch_all_recordings:
        {"{order}||{title}": [{"ms", "id", "performer"}, ...]}
    """
    # A 36-char MBID looks like a UUID; anything else is treated as a search query.
    if len(work_id_or_query) == 36 and work_id_or_query.count("-") == 4:
        seed_id = work_id_or_query
    else:
        candidates = search_work(work_id_or_query, limit=1)
        if not candidates:
            return {}
        seed_id = candidates[0]["id"]

    parent = find_parent_work(seed_id)
    movements = fetch_movements(parent["id"])

    out: dict[str, list[dict]] = {}
    for m in movements:
        recs = fetch_recordings_for_movement(m["id"])
        if on_movement:
            on_movement(m["title"], len(recs))
        key = f"{m['order']}||{m['title']}"
        out[key] = recs

    out["_title"] = parent["title"]
    return out
