"""Diagnostic only: apply the cache plan to a stopped QEMU locationd process.

Run with the host LLDB attached to the VM, stopped at CoreRoutine's first
-[RTRoutineManager _createConnection]. No host process or image is patched.
"""
from pathlib import Path
import struct
import sys
import lldb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
import core_routine

target = lldb.debugger.GetSelectedTarget()
process = target.GetProcess()
assert process.GetState() == lldb.eStateStopped
modules = [m for m in target.modules if m.GetFileSpec().GetFilename() == 'CoreRoutine']
assert len(modules) == 1
header = modules[0].GetObjectFileHeaderAddress()
base = header.GetFileAddress()
slide = header.GetLoadAddress(target)-base


def read(address, size):
    error = lldb.SBError()
    data = process.ReadMemory(address+slide, size, error)
    assert error.Success() and len(data) == size, str(error)
    return data


plan = core_routine.checked_edits(read, base)
stub = base+core_routine.STUB
instruction = read(stub, 6)
assert instruction[:2] == b'\xff\x25', instruction.hex()
slot = stub+6+struct.unpack('<i', instruction[2:])[0]
callee = struct.unpack('<Q', read(slot, 8))[0]
symbol = target.ResolveLoadAddress(callee).GetSymbol().GetName()
assert symbol == 'SCDynamicStoreCopyConsoleUser', symbol
print('Verified native console-user API:', symbol, hex(callee))
for address, original, replacement, reason in plan:
    error = lldb.SBError()
    written = process.WriteMemory(address+slide, replacement, error)
    assert error.Success() and written == len(replacement), str(error)
    assert read(address, written) == replacement
    print('Applied private-page diagnostic edit:', hex(address+slide), reason)
