"""Per-method summary of an objcdis.py dump of MeRGBW for iOS.

For each method: the command byte(s) it passes to the app's frame builder (`commadnWithHeader`,
the app's own spelling), the literals it references, and the calls it makes.
Usage: summarize.py DUMP.txt
"""

import re
import sys

SKIP = {"length", "manager"}
cur, buf, out = None, [], []


def flush():
    if not cur:
        return
    cmds, last = [], {}
    for line in buf:
        m = re.search(r"mov\s+w(\d+), #(0x[0-9a-f]+|\d+)", line)
        if m:
            last[m.group(1)] = int(m.group(2), 0)
        if "commadnWithHeader" in line:
            c = last.get("3")
            cmds.append(hex(c) if c is not None else "?")
    calls = [re.search(r"-> \[(.*?)\]", x).group(1) for x in buf if "-> [" in x]
    lits = [x.split("; ", 1)[1].strip() for x in buf if re.search(r"; (@'|'|const:)", x)]
    out.append(
        f"{cur}\n   cmd={cmds} lits={lits[:8]}\n   calls={[c for c in calls if c not in SKIP][:16]}"
    )


with open(sys.argv[1]) as f:
    for line in f:
        if line.startswith("###"):
            flush()
            cur, buf = line.split(" @")[0][4:], []
        else:
            buf.append(line)
flush()
print("\n".join(out))
