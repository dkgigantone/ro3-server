"""Pack the custom files (hats, models, icons, settings) into one scrambled file: <client>\\ro3emu\\eclipse.dat

Usage: python pack_custom.py <client folder>          e.g. C:\\Games\\EclipseClient
Writes <client>\\ro3emu\\eclipse.dat. It does not delete anything; the release script removes the loose copies.
The secret must match CustomPack.cs.
"""
import hashlib, hmac, os, struct, sys

SECRET = "Eclipse|custom|pack|1"
PACK = "eclipse.dat"
# folders under ro3emu that go into the pack, and single files
FOLDERS = ["hats", "custom"]
FILES = ["hat-options.txt"]


def keystream(key, nonce, start, count):
    out = bytearray()
    n = start // 32
    skip = start % 32
    while len(out) < count + skip:
        out += hmac.new(key, nonce + struct.pack("<q", n), hashlib.sha256).digest()
        n += 1
    return bytes(out[skip:skip + count])


def crypt(key, nonce, data, start):
    ks = keystream(key, nonce, start, len(data))
    return bytes(a ^ b for a, b in zip(data, ks))


def collect(root):
    found = {}
    for folder in FOLDERS:
        for base, _, names in os.walk(os.path.join(root, folder)):
            for n in names:
                full = os.path.join(base, n)
                rel = os.path.relpath(full, root).replace("\\", "/").lower()
                found[rel] = full
    for f in FILES:
        full = os.path.join(root, f)
        if os.path.isfile(full):
            found[f.lower()] = full
    return found


def main():
    if len(sys.argv) != 2:
        print(__doc__); return 1
    root = os.path.join(sys.argv[1], "ro3emu")
    if not os.path.isdir(root):
        print("no ro3emu folder in", sys.argv[1]); return 1
    files = collect(root)
    key = hashlib.sha256(SECRET.encode()).digest()
    nonce = os.urandom(16)
    datas, entries, offset = [], [], 0
    for rel in sorted(files):
        d = open(files[rel], "rb").read()
        entries.append((rel.encode(), offset, len(d)))
        datas.append(d); offset += len(d)
    index = struct.pack("<i", len(entries))
    for name, off, ln in entries:
        index += struct.pack("<i", len(name)) + name + struct.pack("<qi", off, ln)
    # payload = index + data, one keystream; data offsets are relative to the end of the index
    # CustomPack.cs reads data at HeaderSize + entryOffset, so shift offsets by the index length
    shifted = struct.pack("<i", len(entries))
    for name, off, ln in entries:
        shifted += struct.pack("<i", len(name)) + name + struct.pack("<qi", off + len(index), ln)
    assert len(shifted) == len(index)
    payload = shifted + b"".join(datas)
    header = b"ECP1" + nonce + struct.pack("<i", len(index))
    out = os.path.join(root, PACK)
    with open(out, "wb") as f:
        f.write(header + crypt(key, nonce, payload, 0))
    print("packed %d files (%.1f MB) into %s" % (len(entries), len(payload) / 1048576, out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
