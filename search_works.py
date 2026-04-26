import requests

SEARCH_ENDPOINTS = [
    "https://classical.music.apple.com/api/classical/v7/search?q={query}&l=en-US",
    "https://classical.music.apple.com/api/classical/v7/query/view/us/search?q={query}&types=works&l=en-US",
]


def _extract_works_from_search_response(data: dict) -> list:
    candidates = []

    if isinstance(data.get("items"), list):
        candidates = data["items"]
    elif isinstance(data.get("results"), dict):
        works_bucket = data["results"].get("works", {})
        if isinstance(works_bucket.get("items"), list):
            candidates = works_bucket["items"]
    elif isinstance(data.get("sections"), list) and data["sections"]:
        section = data["sections"][0]
        components = section.get("components", [])
        if components and isinstance(components[0].get("items"), list):
            candidates = components[0]["items"]

    return [c for c in candidates if "id" in c]


def search_works(query: str) -> None:
    encoded_query = requests.utils.quote(query)
    for url_template in SEARCH_ENDPOINTS:
        url = url_template.format(query=encoded_query)
        try:
            response = requests.get(url, timeout=10)
            if response.status_code != 200:
                continue
            data = response.json()
        except (requests.RequestException, ValueError):
            continue

        works = _extract_works_from_search_response(data)
        if works:
            print(f"Found {len(works)} result(s):\n")
            for w in works:
                print(f"  ID:    {w['id']}")
                print(f"  Title: {w.get('title', '(unknown)')}")
                print()
            return

    print(
        "No search endpoint returned usable results.\n"
        "Try browsing https://classical.music.apple.com and copying the work ID from the URL.\n"
        "Work IDs follow the pattern: {composer-slug}-{birth-year}-pp{number}"
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Search Apple Music Classical for a work by name.")
    parser.add_argument("query", help="Search query, e.g. 'beethoven fur elise'")
    args = parser.parse_args()
    search_works(args.query)
