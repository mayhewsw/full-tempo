import argparse
import json
from collections import defaultdict
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import numpy as np

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


def parse_args():
    parser = argparse.ArgumentParser(
        description="Plot duration distributions for each movement in a work JSON file."
    )
    parser.add_argument("json_file", help="Path to the work JSON file, e.g. ludwig-van-beethoven-1770-pp252.json")
    parser.add_argument("--sidecar", help="Path to YAML sidecar config (default: same name as JSON with .yaml extension)")
    parser.add_argument("-o", "--output", help="Save the figure to this path instead of opening an interactive window")
    return parser.parse_args()


def load_movements(data):
    """
    Group keys by their integer index prefix (the N in "N||title").
    Returns list of (index, display_title, [durations_ms]) sorted by index.
    Handles title variation across recordings by grouping all titles for a given
    index and picking the most common one.
    """
    by_index = defaultdict(lambda: defaultdict(list))
    for key, entries in data.items():
        if "||" not in key:
            continue  # e.g. the "_title" metadata key added by app.py's cache
        idx_str, title = key.split("||", 1)
        by_index[int(idx_str)][title].extend(entries)

    movements = []
    for idx in sorted(by_index.keys()):
        title_groups = by_index[idx]
        best_title = max(title_groups, key=lambda t: len(title_groups[t]))
        all_entries = [e for entries in title_groups.values() for e in entries]
        durations_ms = [e["ms"] for e in all_entries]
        movements.append((idx, best_title, durations_ms))

    return movements


def load_sidecar(path):
    if not HAS_YAML:
        print("Warning: PyYAML not installed — install it with 'pip install pyyaml' to enable BPM overlays.")
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def plot_movements(movements, sidecar, piece_title, output=None):
    n = len(movements)
    fig, axes = plt.subplots(nrows=1, ncols=n, figsize=(6 * n, 5))
    if n == 1:
        axes = [axes]
    fig.suptitle(f"{piece_title}: Movement Duration Distributions")

    default_colors = plt.rcParams['axes.prop_cycle'].by_key()['color']
    movement_configs = sidecar.get("movements", [])

    for i, (ax, (idx, auto_title, durations)) in enumerate(zip(axes, movements)):
        config = movement_configs[i] if i < len(movement_configs) else {}
        color = config.get("color", default_colors[i % len(default_colors)])
        title = config.get("title", auto_title)

        durations_s = np.array(durations) / 1000.0
        ax.hist(durations_s, bins=30, alpha=0.6, edgecolor='black', color=color)

        median_s = np.median(durations_s)
        bars = config.get("bars")
        beats_per_bar = config.get("beats_per_bar")
        target_bpm = config.get("target_bpm")

        if bars and beats_per_bar:
            median_bpm = 60 * bars * beats_per_bar / median_s
            ax.axvline(x=median_s, color=color, linestyle='--',
                       label=f"median: {median_bpm:.1f} BPM")
            if target_bpm:
                expected_s = 60 * bars * beats_per_bar / target_bpm
                ax.axvline(x=expected_s, color=color, linestyle=':',
                           label=f"target: {target_bpm} BPM")
        else:
            ax.axvline(x=median_s, color=color, linestyle='--',
                       label=f"median: {median_s:.1f}s")

        ax.set_title(title)
        ax.set_xlabel("Duration (s)")
        ax.set_ylabel("Frequency")
        ax.legend()

    plt.tight_layout(rect=[0, 0, 1, 0.95])
    if output:
        fig.savefig(output, dpi=150)
        print(f"Saved to {output}")
    else:
        plt.show()


def main():
    args = parse_args()
    if args.output:
        matplotlib.use("Agg")
    json_path = Path(args.json_file)

    with open(json_path) as f:
        data = json.load(f)

    movements = load_movements(data)
    print(f"Detected {len(movements)} movement(s):")
    for idx, title, durations in movements:
        print(f"  {idx}: {title!r}  ({len(durations)} recordings)")

    sidecar_path = Path(args.sidecar) if args.sidecar else json_path.with_suffix(".yaml")
    sidecar = {}
    if sidecar_path.exists():
        print(f"Loading sidecar: {sidecar_path}")
        sidecar = load_sidecar(sidecar_path)
    elif args.sidecar:
        print(f"Warning: sidecar {args.sidecar} not found, proceeding without BPM metadata.")

    piece_title = sidecar.get("title", json_path.stem)
    plot_movements(movements, sidecar, piece_title, output=args.output)


if __name__ == "__main__":
    main()
