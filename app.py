import requests
import os
import json
import time
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory

from core import fetch_all_recordings
from search_works import SEARCH_ENDPOINTS, _extract_works_from_search_response
import musicbrainz_db_core

app = Flask(__name__, static_folder="static")

# In-memory cache: work_id -> enriched duration dict
_cache = {}

CACHE_DIR = Path(os.environ.get("WORK_CACHE_DIR", ".cache/work"))
CACHE_TTL_S = int(os.environ.get("WORK_CACHE_TTL_SECONDS", str(7 * 24 * 60 * 60)))  # 7 days


def _cache_path_for_work(work_id: str) -> Path:
    safe = "".join(c for c in work_id if c.isalnum() or c in ("-", "_"))
    return CACHE_DIR / f"{safe}.json"


def _read_work_cache(path: Path):
    try:
        st = path.stat()
    except FileNotFoundError:
        return None

    if CACHE_TTL_S > 0 and (time.time() - st.st_mtime) > CACHE_TTL_S:
        return None

    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _write_work_cache(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload))
    tmp.replace(path)


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/search")
def search():
    query = request.args.get("q", "").strip()
    source = request.args.get("source", "apple").strip().lower()
    if not query:
        return jsonify({"results": []})

    if source == "musicbrainz":
        results = musicbrainz_db_core.search_work(query, limit=20)
        return jsonify({
            "results": [
                {"id": r["id"], "title": r["title"], "recording_count": r["recording_count"]}
                for r in results
            ]
        })

    encoded = requests.utils.quote(query)
    for url_template in SEARCH_ENDPOINTS:
        url = url_template.format(query=encoded)
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code != 200:
                continue
            data = resp.json()
        except (requests.RequestException, ValueError):
            continue

        works = _extract_works_from_search_response(data)
        if works:
            return jsonify({
                "results": [{"id": w["id"], "title": w.get("title", w["id"])} for w in works]
            })

    return jsonify({"results": []})


def _fetch_work_title(work_id: str) -> str:
    url = (
        f"https://classical.music.apple.com/api/classical/v7/query/view/us"
        f"/work/{work_id}?l=en-US"
    )
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            header = resp.json().get("header", {})
            title = header.get("title", "")
            composer = header.get("composerName", "")
            if composer and title:
                return f"{composer}: {title}"
            return title or work_id
    except Exception:
        pass
    return work_id


@app.route("/api/work/<work_id>")
def work_durations(work_id):
    source = request.args.get("source", "apple").strip().lower()
    cache_key = f"{source}:{work_id}" if source == "musicbrainz" else work_id

    if cache_key in _cache:
        return jsonify(_cache[cache_key])

    cache_path = _cache_path_for_work(cache_key)
    cached = _read_work_cache(cache_path)
    if cached is not None:
        _cache[cache_key] = cached
        return jsonify(cached)

    if source == "musicbrainz":
        data = musicbrainz_db_core.fetch_all_recordings(work_id)
    else:
        data = fetch_all_recordings(work_id)
        data["_title"] = _fetch_work_title(work_id)

    _cache[cache_key] = data
    _write_work_cache(cache_path, data)
    return jsonify(data)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=True)
