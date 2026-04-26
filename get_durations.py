import argparse
import json

from core import fetch_all_recordings


def parse_args():
    parser = argparse.ArgumentParser(
        description="Fetch Apple Music Classical recording duration data for a work."
    )
    parser.add_argument(
        "work_id",
        help="Work ID slug, e.g. ludwig-van-beethoven-1770-pp252",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    work_id = args.work_id
    print(f"Fetching recordings for work: {work_id}")

    def on_page(page_num, count):
        print(f"Page {page_num}: {count} recordings — fetching details in parallel...")

    titles_to_duration = fetch_all_recordings(work_id, on_page=on_page)

    output_path = f"{work_id}.json"
    with open(output_path, "w") as f:
        f.write(json.dumps(titles_to_duration, indent=4))
    print(f"\nWritten to {output_path} ({len(titles_to_duration)} track keys)")


if __name__ == "__main__":
    main()
