#!/usr/bin/env python3
"""edit-metal.py IN OUT: OCLP's Metal 3802 wrapper (Moraea shim) -> a wrapper that plays hello, before re-signing.
  0xb856 01 -> 00   _helloSetup's +[LCAUtilities shouldSkipHello] stub returns NO (Language Chooser draws hello)
  0xb881 74 -> eb   _helloSetup: je -> jmp, Setup Assistant is not swizzled (Apple's own hello / Welcome logic runs)
  + LC_LOAD_WEAK_DYLIB /usr/lib/libhelloblur.dylib in the zero padding after the load commands (add_weak_dylib.py)
Each edit checks the bytes it replaces; another shim stops here with an AssertionError. Then: codesign -f -s - -i Metal OUT.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import add_weak_dylib

EDITS = [(0xb856, b"\x01", b"\x00"), (0xb881, b"\x74", b"\xeb")]
src, dst = sys.argv[1:3]
b = bytearray(open(src, "rb").read())
for off, old, new in EDITS:
    assert b[off:off + len(old)] == old, f"{src}: {b[off:off + len(old)].hex()} at {off:#x}, expected {old.hex()}"
    b[off:off + len(new)] = new
open(dst + ".edited", "wb").write(b)
add_weak_dylib.main(dst + ".edited", dst, "/usr/lib/libhelloblur.dylib")
os.remove(dst + ".edited")
