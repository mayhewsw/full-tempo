import matplotlib.pyplot as plt
import numpy as np
import json

with open("johann-sebastian-bach-1685-pp19.json") as f:
    data = json.load(f)

# plot these keys
piece_name = "Bach Double"
plot_keys = ["0||I. Vivace", "1||II. Largo ma non tanto", "2||III. Allegro"]
movement_data = [
    {"names": ["0||I. Vivace"], "bars": 88, "beats_per_bar": 4, "length_data": [], "target_bpm": 96, "color": "red"},
    {"names": ["1||II. Largo ma non tanto"], "bars": 50, "beats_per_bar": 4, "length_data": [], "target_bpm": 31, "color": "blue"},
    {"names": ["2||III. Allegro"], "bars": 154, "beats_per_bar": 3, "length_data": [], "target_bpm": 100, "color": "green"},
]

# Fill movement_data
for key in plot_keys:
    if "vivace" in key.lower():
        movement_data[0]["length_data"].extend(data[key])
    elif "largo ma non tanto" in key.lower():
        movement_data[1]["length_data"].extend(data[key])
    elif "allegro" in key.lower():
        movement_data[2]["length_data"].extend(data[key])
    else:
        raise ValueError(f"Unknown movement: {key}")

# --- SIDE-BY-SIDE SUBPLOTS ---
fig, axes = plt.subplots(nrows=1, ncols=3, figsize=(18, 5))
fig.suptitle(f"{piece_name}: Histograms of Movement Durations (in Seconds)")

for ax, m in zip(axes, movement_data):
    color = m["color"]
    durations = np.array(m["length_data"]) / 1000.0
    movement = m["names"][0]

    # Histogram
    ax.hist(durations, bins=30, alpha=0.6,
            edgecolor='black', color=color)

    # Median and target lines
    median_duration = np.median(durations)
    median_bpm = 60 * m["bars"] * m["beats_per_bar"] / median_duration
    expected_duration = 60 * m["bars"] * m["beats_per_bar"] / m["target_bpm"]

    ax.axvline(x=median_duration, color=color, linestyle='--', label=f"median: {median_bpm:.2f} BPM")
    ax.axvline(x=expected_duration, color=color, linestyle=':', label=f"target: {m['target_bpm']} BPM")

    # Labels
    ax.set_title(movement)
    ax.set_xlabel("Duration (s)")
    ax.set_ylabel("Frequency")
    ax.legend()

plt.tight_layout(rect=[0, 0, 1, 0.95])  # leave room for suptitle
plt.show()
