import requests
import re
import unicodedata
import threading
from dataclasses import dataclass
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from rapidfuzz import fuzz

WORK_URL_TEMPLATE = (
    "https://classical.music.apple.com/api/classical/v7/query/view/us"
    "/work/{WORK_ID}/recordings/page/{PAGE_ID}"
    "?sortBy=popularityByContributors&sortOrder=descending&q=&l=en-US"
)
RECORDING_URL_TEMPLATE = (
    "https://classical.music.apple.com/api/classical/v7/query/view/us"
    "/recording/{RECORDING_ID}?l=en-US"
)
MAX_NUM_PAGES = 300
MAX_WORKERS = 24


_BRACKETED_RE = re.compile(r"(\([^)]*\)|\[[^\]]*\])")
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")

_thread_local = threading.local()


def _get_session() -> requests.Session:
    """
    Each worker thread gets its own Session for connection pooling.
    """
    sess = getattr(_thread_local, "session", None)
    if sess is None:
        sess = requests.Session()
        setattr(_thread_local, "session", sess)
    return sess


def _roman_to_int(token: str) -> int | None:
    """
    Best-effort roman numeral parsing for tokens like I, II, IV, VIII, etc.
    Returns None if token isn't a plausible roman numeral.
    """
    s = token.upper()
    if not s or any(c not in "IVXLCDM" for c in s):
        return None

    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    prev = 0
    for c in reversed(s):
        v = values[c]
        if v < prev:
            total -= v
        else:
            total += v
            prev = v

    # Guard rails: we only expect movement-ish small numerals in titles.
    if 1 <= total <= 40:
        return total
    return None


def _normalize_track_title(title: str) -> str:
    """
    Normalization used ONLY for clustering/matching.
    Intentionally does NOT drop musically meaningful words (e.g. major/minor).
    """
    # Heuristic: if the string includes a work prefix like
    # "Violin Concerto in D minor: I. Allegro", keep the movement-like suffix.
    # This reduces false merges where the shared work name dominates similarity.
    movementish = re.compile(r"^\s*([ivxlcdm]+|\d+)\b", re.IGNORECASE)
    parts = re.split(r"\s*[:—–-]\s*", title)
    if len(parts) > 1:
        for p in reversed(parts):
            if movementish.search(p.strip()):
                title = p.strip()
                break

    t = unicodedata.normalize("NFKD", title)
    t = t.lower()
    t = _BRACKETED_RE.sub(" ", t)  # remove (...) and [...]
    t = t.replace("—", " ").replace("–", " ").replace("-", " ").replace(":", " ")

    # Canonicalize roman numerals token-by-token into numbers (helps matching I / i. / I:)
    tokens = []
    for raw in re.split(r"\s+", t):
        tok = raw.strip(" .,:;")
        if not tok:
            continue
        n = _roman_to_int(tok)
        tokens.append(str(n) if n is not None else tok)

    t = " ".join(tokens)
    t = _NON_WORD_RE.sub(" ", t).strip()
    t = re.sub(r"\s+", " ", t)
    return t


@dataclass
class _TrackOcc:
    recording_id: str
    track_index: int
    title: str
    performer: str
    norm_title: str
    duration_ms: int


def fetch_recording_details(recording_id: str) -> list[_TrackOcc]:
    """
    Returns per-track occurrences for a recording.
    Thread-safe: produces a new list, no shared state.
    """
    url = RECORDING_URL_TEMPLATE.format(RECORDING_ID=recording_id)
    response = _get_session().get(url, timeout=15)
    recording_j = response.json()

    track_list_object = recording_j['sections'][0]
    assert track_list_object['type'] == 'track-list'
    track_list = track_list_object['components'][0]
    assert track_list['type'] == 'list'

    result: list[_TrackOcc] = []
    for i, track in enumerate(track_list['items']):
        title = track.get("title", "").strip()
        if not title:
            continue
        performer = (track.get("subtitle") or "").strip()
        duration_ms = int(track.get("durationMs") or 0)
        result.append(
            _TrackOcc(
                recording_id=recording_id,
                track_index=i,
                title=title,
                performer=performer,
                norm_title=_normalize_track_title(title),
                duration_ms=duration_ms,
            )
        )
    return result


def _median_int(vals: list[int]) -> int:
    if not vals:
        return 0
    s = sorted(vals)
    return s[len(s) // 2]


def _cluster_tracks(track_occs: list[_TrackOcc]) -> dict:
    """
    Cluster track occurrences into movement buckets based on normalized titles,
    with track index acting as a weak prior to prevent over-merging.
    Returns dict keyed by '{clusterOrder}||{displayTitle}' -> [{ms,id}, ...]
    """
    if not track_occs:
        return {}

    # Aggregate occurrences by normalized title to reduce comparisons.
    by_norm: dict[str, list[_TrackOcc]] = defaultdict(list)
    for occ in track_occs:
        if occ.norm_title:
            by_norm[occ.norm_title].append(occ)

    norm_items = sorted(by_norm.items(), key=lambda kv: len(kv[1]), reverse=True)

    clusters: list[dict] = []
    threshold = 88  # starting point; can tune per corpus
    index_boost = 3

    for norm, occs in norm_items:
        norm_indices = [o.track_index for o in occs]
        norm_med_idx = _median_int(norm_indices)

        best_i = None
        best_score = -1.0
        for ci, c in enumerate(clusters):
            score = float(fuzz.token_set_ratio(norm, c["rep_norm"]))
            if abs(norm_med_idx - c["median_index"]) <= 1:
                score += index_boost
            if score > best_score:
                best_score = score
                best_i = ci

        if best_i is not None and best_score >= threshold:
            c = clusters[best_i]
            c["norms"].append(norm)
            c["count"] += len(occs)
            c["indices"].extend(norm_indices)
            c["median_index"] = _median_int(c["indices"])
            # Update representative to the most frequent normalized title within the cluster.
            c["norm_counts"][norm] += len(occs)
            c["rep_norm"] = max(c["norm_counts"].items(), key=lambda it: it[1])[0]
        else:
            clusters.append(
                {
                    "rep_norm": norm,
                    "norms": [norm],
                    "count": len(occs),
                    "indices": list(norm_indices),
                    "median_index": norm_med_idx,
                    "norm_counts": defaultdict(int, {norm: len(occs)}),
                }
            )

    # Determine display title per cluster (most common original title among its norms).
    cluster_display: list[tuple[int, str, set[str]]] = []
    for ci, c in enumerate(clusters):
        title_counts: dict[str, int] = defaultdict(int)
        for norm in c["norms"]:
            for occ in by_norm[norm]:
                title_counts[occ.title] += 1
        display = max(title_counts.items(), key=lambda it: it[1])[0] if title_counts else c["rep_norm"]
        cluster_display.append((ci, display, set(c["norms"])))

    # Order clusters by median observed index to preserve typical movement order.
    ordered = sorted(
        [(ci, c["median_index"]) for ci, c in enumerate(clusters)],
        key=lambda it: it[1],
    )
    order_map = {ci: order for order, (ci, _) in enumerate(ordered)}

    # Build output buckets.
    out: dict[str, list[dict]] = defaultdict(list)
    for ci, display, norms in cluster_display:
        cluster_order = order_map[ci]
        key = f"{cluster_order}||{display}"
        for norm in norms:
            for occ in by_norm[norm]:
                out[key].append(
                    {"ms": occ.duration_ms, "id": occ.recording_id, "performer": occ.performer}
                )

    return dict(out)


def fetch_work_info(work_id: str) -> dict:
    """
    Returns display metadata for a work: title, subtitle, composer.
    Falls back gracefully if the endpoint doesn't respond as expected.
    """
    url = WORK_INFO_URL_TEMPLATE.format(WORK_ID=work_id)
    try:
        resp = requests.get(url, timeout=10)
        d = resp.json()
        header = d.get("header", {})
        title = header.get("title", "")
        subtitle = header.get("subtitle", "")
        composer = header.get("composerName", "")
        display = title
        if subtitle:
            display = f"{title} ({subtitle})"
        if composer:
            display = f"{composer}: {display}"
        return {"title": display or work_id, "publicUrl": d.get("publicUrl", "")}
    except Exception:
        return {"title": work_id, "publicUrl": ""}


def fetch_all_recordings(work_id: str, on_page=None) -> dict:
    """
    Fetches all recordings for a work, returning enriched duration data.
    on_page: optional callback(page_num, count) called after each page is fetched.
    """
    all_tracks: list[_TrackOcc] = []
    page_session = requests.Session()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        for current_page in range(MAX_NUM_PAGES):
            url = WORK_URL_TEMPLATE.format(WORK_ID=work_id, PAGE_ID=current_page)
            response = page_session.get(url, timeout=15)
            page_j = response.json()

            items = page_j.get('items', [])
            if not items:
                break

            if on_page:
                on_page(current_page, len(items))

            futures = {
                executor.submit(fetch_recording_details, item['id']): item['id']
                for item in items
            }

            for future in as_completed(futures):
                rid = futures[future]
                try:
                    all_tracks.extend(future.result())
                except Exception as e:
                    print(f"  Warning: failed to fetch recording {rid}: {e}")

    return _cluster_tracks(all_tracks)
