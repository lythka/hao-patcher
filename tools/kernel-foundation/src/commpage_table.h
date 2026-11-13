// IntelCommpage's collection address table, __DATA,__ic_table (version 1).
// Every kernel collection address IntelCommpage uses (kernel functions and globals, the commpage alias and map
// slots, the hook sites and their first 14 bytes, the IOSurface member) lives here instead of in the code, so the
// same kext serves any layout kmutil gives the same kernel. commpage_build.py writes the reference collection's
// values (IC_TABLE_REFERENCE in commpage_abi.hpp); tools/kc-binder rewrites the section for the collection an image
// boots. The kext checks magic, version, size, slot counts, slot names (layout) and the kernel UUID before it uses
// any entry. The Python side of this layout is commpage_build.py (table_bytes, read_table).
#pragma once
#include <stdint.h>

struct IntelCommpageTable {
    uint32_t magic, version, size;
    uint8_t counts[4];                          // symbols, alias slots, map slots, hooks
    uint8_t layout[16];                         // first 16 bytes of the sha256 of the slot names
    uint8_t kernel_uuid[16];
    uintptr_t kernel_header, iosurface_header, iosurface_set_value;
    uintptr_t symbols[ic_symbol_count];         // IC_SYMBOLS order
    uintptr_t alias_slots[ic_alias_count];
    uintptr_t map_slots[ic_map_count];
    uintptr_t hook_sites[ic_hook_count];
    uint8_t hook_original[ic_hook_count][14];
};
static_assert(sizeof(IntelCommpageTable) == ic_table_size, "IntelCommpage table layout (commpage_build.py)");
static_assert(__builtin_offsetof(IntelCommpageTable, symbols) == 72, "IntelCommpage table header");
