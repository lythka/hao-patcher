#!/usr/bin/env python3
"""edit-gva.py IN OUT: OCLP's AppleGVA (Big Sur 11.7.10, "Big Sur GVA") -> one that reports Intel hardware JPEG
decode as unavailable, before re-signing.
  _AVFJPG_GetJPEGDecodeAvailability entry: push rbp; mov rbp,rsp -> xor eax,eax; ret
  (0 is what the function returns itself when the model is unsupported), so ImageIO decodes JPEG in software.
The edit checks the bytes it replaces; another AppleGVA stops here with an AssertionError.
Then: codesign -f -s - --preserve-metadata=identifier,entitlements OUT.
"""
import sys

EDITS = [(0x415f, bytes.fromhex("554889"), bytes.fromhex("31c0c3"))]
src, dst = sys.argv[1:3]
b = bytearray(open(src, "rb").read())
for off, old, new in EDITS:
    assert b[off:off + len(old)] == old, f"{src}: {b[off:off + len(old)].hex()} at {off:#x}, expected {old.hex()}"
    b[off:off + len(new)] = new
open(dst, "wb").write(b)
