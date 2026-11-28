"""Print every string in the string table of each classes*.dex in an APK, prefixed by dex name.
Usage: dexstrings.py APK"""

import struct
import sys
import zipfile


def uleb(b, i):
    r = s = 0
    while True:
        x = b[i]
        i += 1
        r |= (x & 0x7F) << s
        s += 7
        if x < 0x80:
            return r, i


z = zipfile.ZipFile(sys.argv[1])
for name in sorted(n for n in z.namelist() if n.startswith("classes") and n.endswith(".dex")):
    d = z.read(name)
    count, off = struct.unpack_from("<II", d, 0x38)
    for k in range(count):
        p = struct.unpack_from("<I", d, off + 4 * k)[0]
        _, p = uleb(d, p)
        e = d.index(b"\0", p)
        print(f"{name}\t{d[p:e].decode('utf-8', 'replace')}")
