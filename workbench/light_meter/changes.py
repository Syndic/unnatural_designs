"""Print region luma changes, plus a heartbeat every 20 s, relative to a target time.

Usage: changes.py <region.csv> <target-epoch>
"""

import csv
import datetime
import sys

target = float(sys.argv[2])
prev = None
with open(sys.argv[1]) as f:
    for r in csv.DictReader(f):
        t = float(r["epoch"])
        luma = 0.2126 * int(r["r"]) + 0.7152 * int(r["g"]) + 0.0722 * int(r["b"])
        if prev is None or abs(luma - prev) > 15 or int(t) % 20 == 0:
            clock = datetime.datetime.fromtimestamp(t).strftime("%H:%M:%S")
            print(
                f"{clock}  t-target={t - target:+7.1f}s  rgb=({r['r']},{r['g']},{r['b']})  "
                f"luma={luma:5.1f}"
            )
        prev = luma
