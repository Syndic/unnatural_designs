"""Mean, standard deviation and range of region luma per labelled time window.

Usage: windows.py <region.csv> <t0-epoch> name@start-end ...
Start and end are seconds after t0, so neither can be negative.
"""

import csv
import statistics
import sys

with open(sys.argv[1]) as f:
    rows = list(csv.DictReader(f))
t0 = float(sys.argv[2])
for spec in sys.argv[3:]:
    name, span = spec.split("@")
    a, b = (float(x) for x in span.split("-"))
    vals, rgbs = [], []
    for r in rows:
        t = float(r["epoch"]) - t0
        if a <= t <= b:
            rr, gg, bb = int(r["r"]), int(r["g"]), int(r["b"])
            vals.append(0.2126 * rr + 0.7152 * gg + 0.0722 * bb)
            rgbs.append((rr, gg, bb))
    if vals:
        mean_rgb = tuple(round(statistics.mean(c[i] for c in rgbs)) for i in range(3))
        print(
            f"{name:14} n={len(vals):3d} rgb~{mean_rgb} luma mean={statistics.mean(vals):6.1f} "
            f"sd={statistics.pstdev(vals):5.2f} range={min(vals):6.1f}-{max(vals):6.1f}"
        )
