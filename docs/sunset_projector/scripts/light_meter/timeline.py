"""Label each region-colour row with the most recent event, such as a command sent.

Reads region.swift's CSV on stdin.
Usage: timeline.py <t0-epoch> <every-nth-row> name@seconds ...
"""

import sys

t0 = float(sys.argv[1])
step = int(sys.argv[2])
events = sorted((float(e.split("@")[1]), e.split("@")[0]) for e in sys.argv[3:])
rows = [line.strip().split(",") for line in sys.stdin if line[:1].isdigit()]
for r in rows[::step]:
    t = float(r[0]) - t0
    label = "-"
    for at, name in events:
        if at <= t:
            label = name
    print(f"{t:6.2f}s {r[1]} rgb=({int(r[2]):3d},{int(r[3]):3d},{int(r[4]):3d})  after: {label}")
