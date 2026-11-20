// Inline x86_64 function replacement without frida's GumInterceptor.
// On macOS 27 x86_64, frida-gum 17.25's interceptor sets up its unwind broker on the first replace; the broker
// replaces the unwinder through the interceptor again and recurses until the stack overflows (swift-capture
// crashed before its first record). These helpers use only frida's x86 relocator and writer and
// gum_memory_patch_code: the target's first instructions move to a trampoline (relocated, RIP-relative operands
// fixed), then the target starts with an absolute jump to the replacement. inline_revert restores the bytes.
#pragma once
#include <frida-gum.h>
#include <cstdio>
#include <cstring>

extern "C" {
typedef struct _GumX86Relocator GumX86Relocator;
GumX86Relocator *gum_x86_relocator_new(gconstpointer input_code, GumX86Writer *output);
guint gum_x86_relocator_read_one(GumX86Relocator *self, const void **instruction);
void gum_x86_relocator_write_all(GumX86Relocator *self);
gboolean gum_x86_relocator_eoi(GumX86Relocator *self);
void gum_x86_relocator_unref(GumX86Relocator *self);
}

struct InlineHook {
  guint8 *target = nullptr;
  guint8 saved[32] = {};
  guint length = 0;
};

static const guint ABSOLUTE_JUMP = 14;  // jmp qword [rip+0]; .quad address

static inline void inline_put_jump(guint8 *at, const void *to) {
  at[0] = 0xff; at[1] = 0x25; std::memset(at + 2, 0, 4);
  auto address = (guint64)to; std::memcpy(at + 6, &address, 8);
}

struct InlinePatch { const guint8 *bytes; guint length; };

static inline void inline_apply(gpointer mem, gpointer data) {
  auto patch = (const InlinePatch *)data;
  std::memcpy(mem, patch->bytes, patch->length);
}

// Replace target with replacement; *original receives a callable trampoline to the old code. false: refused.
static inline bool inline_replace(InlineHook &hook, gpointer target, gpointer replacement, gpointer *original) {
  // Written while RW, then made RX: an RWX page is refused where W^X is enforced.
  auto page = gum_query_page_size();
  auto trampoline = (guint8 *)gum_memory_allocate(nullptr, page, page, GUM_PAGE_RW);
  if (!trampoline) { std::fprintf(stderr, "inline-hook: no trampoline page\n"); return false; }
  auto writer = gum_x86_writer_new(trampoline);
  auto relocator = gum_x86_relocator_new(target, writer);
  guint consumed = 0;
  while (consumed < ABSOLUTE_JUMP) {
    guint total = gum_x86_relocator_read_one(relocator, nullptr);
    if (total == 0 || (gum_x86_relocator_eoi(relocator) && total < ABSOLUTE_JUMP)) {
      gum_x86_relocator_unref(relocator); gum_x86_writer_unref(writer);
      std::fprintf(stderr, "inline-hook: %p ends or cannot be decoded within %u bytes\n", target, ABSOLUTE_JUMP);
      return false;
    }
    consumed = total;
  }
  gum_x86_relocator_write_all(relocator);
  gum_x86_writer_flush(writer);
  guint8 *back = trampoline + gum_x86_writer_offset(writer);
  gum_x86_relocator_unref(relocator); gum_x86_writer_unref(writer);
  inline_put_jump(back, (guint8 *)target + consumed);
  if (consumed > sizeof(hook.saved)) { std::fprintf(stderr, "inline-hook: %u bytes moved\n", consumed); return false; }
  if (!gum_try_mprotect(trampoline, page, GUM_PAGE_RX)) {
    std::fprintf(stderr, "inline-hook: trampoline cannot be made executable\n"); return false;
  }
  hook.target = (guint8 *)target; hook.length = consumed;
  std::memcpy(hook.saved, target, consumed);
  guint8 entry[32];
  inline_put_jump(entry, replacement);
  std::memset(entry + ABSOLUTE_JUMP, 0x90, consumed - ABSOLUTE_JUMP);  // nop the rest of the moved instructions
  InlinePatch patch = {entry, consumed};
  if (!gum_memory_patch_code(target, consumed, inline_apply, &patch)) {
    std::fprintf(stderr, "inline-hook: %p cannot be patched\n", target); return false;
  }
  *original = trampoline;
  return true;
}

static inline void inline_revert(InlineHook &hook) {
  if (!hook.target) return;
  InlinePatch patch = {hook.saved, hook.length};
  gum_memory_patch_code(hook.target, hook.length, inline_apply, &patch);
  hook.target = nullptr;
}
