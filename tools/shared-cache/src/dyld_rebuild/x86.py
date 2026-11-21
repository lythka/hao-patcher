"""Bounded x86_64 decoding for read-only cache-reference evidence.

LLVM is the instruction decoder. A byte displacement candidate is never itself
an instruction boundary. Linear decoding from a supplied start establishes only
that decoding path, not control flow or runtime reachability.
"""
import ctypes
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
import re
import struct
from .exports import require

LTO = '/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/lib/libLTO.dylib'


REPO = Path(__file__).resolve().parents[4]


def row_library(repo=REPO):
    """Resolve the optional shipped decoder, verifying the input row before loading."""
    row = repo / 'pipeline/inputs/common/llvm_disasm'
    if not row.exists():
        return None
    lines = [line.split('#', 1)[0].split() for line in row.read_text().splitlines()]
    lines = [line for line in lines if line]
    require(len(lines) == 1 and len(lines[0]) == 2, 'invalid llvm_disasm input row')
    expected, relative = lines[0]
    require(re.fullmatch(r'[0-9a-f]{64}', expected), 'llvm_disasm requires SHA256')
    path = repo / relative
    if not path.exists():
        return None
    with path.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    require(actual == expected, 'llvm_disasm SHA256 mismatch')
    return path


def default_library():
    """The explicit override, else the verified universal decoder, else libLTO."""
    override = os.environ.get('SHARED_CACHE_DISASM')
    if override is not None:
        return Path(override)
    return row_library() or Path(LTO)


def function_starts(data, base, low, high):
    """Decode terminated LC_FUNCTION_STARTS deltas and refuse malformed ranges."""
    require(0 <= base <= low < high <= 2**64, 'invalid function address range')
    result, value, shift, address, terminated = [], 0, 0, base, False
    for byte in data:
        if terminated:
            require(byte == 0, 'nonzero function-start padding')
            continue
        require(shift < 64 and not (shift == 63 and byte & 0x7e), 'function delta overflow')
        value |= (byte & 0x7f) << shift
        if byte & 0x80:
            shift += 7
            continue
        if value == 0:
            terminated = True
        else:
            address += value
            require(low <= address < high, 'function start outside text')
            require(not result or result[-1] < address, 'unordered function starts')
            result.append(address)
        value, shift = 0, 0
    require(terminated and shift == 0, 'unterminated function starts')
    return result


@dataclass(frozen=True)
class Instruction:
    address: int
    data: bytes
    text: str

    @property
    def end(self):
        return self.address + len(self.data)

    @property
    def direct_target(self):
        # Only unprefixed rel32 CALL/JMP; other branch forms remain uncovered.
        if len(self.data) == 5 and self.data[0] in (0xe8, 0xe9):
            return (self.end + struct.unpack_from("<i", self.data, 1)[0]) & (2**64 - 1)
        return None

    @property
    def rip_target(self):
        # LLVM's default AT&T output prints signed decimal RIP displacements.
        matches = re.findall(r'(?<![\w])(-?(?:0x[0-9a-fA-F]+|\d+))?\(%rip\)', self.text)
        require(len(matches) <= 1, 'multiple RIP operands')
        if not matches:
            return None
        return (self.end + int(matches[0] or '0', 0 if 'x' in matches[0] else 10)) & (2**64 - 1)


class Disassembler:
    def __init__(self, library=None):
        self.path = default_library() if library is None else Path(library)
        self.lib = ctypes.CDLL(str(self.path))
        self.lib.lto_initialize_disassembler.argtypes = []
        self.lib.lto_initialize_disassembler.restype = None
        self.lib.lto_initialize_disassembler()
        self.lib.LLVMCreateDisasm.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]
        self.lib.LLVMCreateDisasm.restype = ctypes.c_void_p
        self.lib.LLVMDisasmInstruction.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint64, ctypes.c_uint64, ctypes.c_char_p, ctypes.c_size_t]
        self.lib.LLVMDisasmInstruction.restype = ctypes.c_size_t
        self.lib.LLVMDisasmDispose.argtypes = [ctypes.c_void_p]
        self.lib.LLVMDisasmDispose.restype = None
        self.context = self.lib.LLVMCreateDisasm(b'x86_64-apple-macos', None, 0, None, None)
        require(self.context, 'LLVM cannot initialize x86_64 disassembler')

    def decode(self, data, address):
        require(0 <= address < 2**64 and address + len(data) <= 2**64, 'instruction range overflow')
        if not data:
            return None
        raw = ctypes.create_string_buffer(bytes(data[:15]))
        output = ctypes.create_string_buffer(2048)
        size = self.lib.LLVMDisasmInstruction(self.context, raw, min(len(data), 15), address, output, len(output))
        require(size <= min(len(data), 15), 'LLVM returned invalid instruction size')
        return Instruction(address, bytes(data[:size]), output.value.decode('utf-8')) if size else None

    def interval(self, data, address):
        result, pos = [], 0
        while pos < len(data):
            instruction = self.decode(data[pos:pos + 15], address + pos)
            if instruction is None:
                return result, address + pos
            result.append(instruction)
            pos += len(instruction.data)
        return result, None

    def close(self):
        if self.context:
            self.lib.LLVMDisasmDispose(self.context)
            self.context = None
