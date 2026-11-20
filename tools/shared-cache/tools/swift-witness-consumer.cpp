// Isolated witness-consumer fixture, not a production witness serializer.
// Swift's pinned ABI headers supply extents and base-protocol requirements.
// Frida Gum handles instruction relocation; no hand-built trampoline is used.
#include "cache-address.h"
#include "witness-runtime.h"
#include "inline-hook.h"
#include "swift/ABI/Metadata.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <unistd.h>
#include <uuid/uuid.h>
#include <vector>
using Word = uintptr_t;
using Descriptor = swift::ProtocolConformanceDescriptor;
typedef const void *__attribute__((swiftcall))
Resolve(const char *, size_t, const void *, const void *const *);
typedef const Word *Witness(const void *, const void *, const void *const *);
typedef const Word *__attribute__((swiftcall)) Conforms(const void *,
                                                        const void *);
extern "C" bool __attribute__((swiftcall))
hao_equatable_compare(const void *, const void *, const void *, const void *);
extern "C" bool __attribute__((swiftcall))
hao_hashable_compare(const void *, const void *, const void *, const void *);
extern "C" intptr_t __attribute__((swiftcall))
hao_hashable_hash(const void *, const void *, const void *);
extern "C" int run_witness_values(void *);
struct Entry {
  const Descriptor *descriptor;
  const void *type;
  const Word *original;
  std::vector<Word> storage;
  unsigned prefix;
  Word *words() { return storage.data() + prefix; }
};
static Entry entries[2];
static Witness *original;
static gpointer implementation;
static unsigned hits, misses, comparisons, hashes;
static uintptr_t cacheBase;
static size_t cacheSize;
static uuid_t cacheUUID;
static bool cacheInfo() {
  auto range = (const void *(*)(size_t *))dlsym(RTLD_DEFAULT,
                                                "_dyld_get_shared_cache_range");
  auto uuid =
      (bool (*)(uuid_t))dlsym(RTLD_DEFAULT, "_dyld_get_shared_cache_uuid");
  if (!range || !uuid || !uuid(cacheUUID))
    return false;
  cacheBase = (uintptr_t)range(&cacheSize);
  return cacheBase && cacheSize;
}
static bool save(const char *path) {
  FILE *f = fopen(path, "w");
  if (!f)
    return false;
  char uuid[37];
  uuid_unparse_lower(cacheUUID, uuid);
  fprintf(f, "%s\n", uuid);
  for (auto &e : entries) {
    fprintf(f, "%zx %u %zu\n", (uintptr_t)e.descriptor - cacheBase, e.prefix,
            e.storage.size());
    for (auto word : e.storage) {
      if (!word) {
        fprintf(f, "z 0 0\n");
        continue;
      }
      bool found = false;
      for (unsigned j = 0; j < 2; j++) {
        auto &other = entries[j];
        auto start = (uintptr_t)(other.original - other.prefix),
             end = (uintptr_t)(other.original + other.storage.size() -
                               other.prefix);
        if (word >= start && word < end) {
          fprintf(f, "e %u %zx\n", j, word - start);
          found = true;
          break;
        }
      }
      if (found)
        continue;
      if (file_backed_cache_pointer(word, cacheBase, cacheBase + cacheSize))
        fprintf(f, "c 0 %zx\n", word - cacheBase);
      else {
        fprintf(stderr, "unsupported fixture pointer %p\n", (void *)word);
        fclose(f);
        return false;
      }
    }
  }
  return fclose(f) == 0;
}
static bool load(const char *path, const void *type) {
  FILE *f = fopen(path, "r");
  if (!f)
    return false;
  char uuid[37];
  uuid_t actual;
  if (fscanf(f, "%36s", uuid) != 1 || uuid_parse(uuid, actual) ||
      memcmp(actual, cacheUUID, 16))
    return false;
  struct Ref {
    unsigned node, word, target;
    size_t offset;
  };
  std::vector<Ref> refs;
  for (unsigned i = 0; i < 2; i++) {
    auto &e = entries[i];
    size_t offset, count;
    if (fscanf(f, "%zx %u %zu", &offset, &e.prefix, &count) != 3 ||
        offset >= cacheSize)
      return false;
    e.descriptor = (const Descriptor *)(cacheBase + offset);
    e.type = type;
    auto g = e.descriptor->getGenericWitnessTable();
    if (!g || e.prefix != g->getWitnessTablePrivateSizeInWords() ||
        count != e.prefix + g->WitnessTableSizeInWords)
      return false;
    e.storage.resize(count);
    for (unsigned k = 0; k < count; k++) {
      char kind;
      unsigned target;
      size_t value;
      if (fscanf(f, " %c %u %zx", &kind, &target, &value) != 3)
        return false;
      if (kind == 'c') {
        if (value >= cacheSize ||
            !file_backed_cache_pointer(cacheBase + value, cacheBase,
                                       cacheBase + cacheSize))
          return false;
        e.storage[k] = cacheBase + value;
      } else if (kind == 'e')
        refs.push_back({i, k, target, value});
      else if (kind != 'z')
        return false;
    }
  }
  for (auto &r : refs) {
    if (r.target >= 2 ||
        r.offset >= entries[r.target].storage.size() * sizeof(Word) ||
        r.offset % sizeof(Word))
      return false;
    entries[r.node].storage[r.word] =
        (uintptr_t)entries[r.target].storage.data() + r.offset;
  }
  return fclose(f) == 0;
}
static const Word *replacement(const void *conformance, const void *type,
                               const void *const *args) {
  for (auto &e : entries)
    if (e.descriptor == conformance && e.type == type) {
      hits++;
      return e.words();
    }
  misses++;
  return original(conformance, type, args);
}
extern "C" bool __attribute__((swiftcall))
check_witness_values(const void *left, const void *right, const void *type,
                     bool expected) {
  if (type != entries[0].type)
    return false;
  for (unsigned i = 0; i < 2; i++) {
    const auto &e = entries[i];
    auto compare = i ? hao_hashable_compare : hao_equatable_compare;
    if (compare(left, right, type, e.original) != expected ||
        compare(left, right, type, entries[i].words()) != expected)
      return false;
    comparisons += 2;
  }
  if (hao_hashable_hash(left, type, entries[1].original) !=
      hao_hashable_hash(left, type, entries[1].words()))
    return false;
  hashes++;
  return true;
}
int main(int argc, char **argv) {
  alarm(30);
  setbuf(stdout, nullptr);
  bool exported = argc == 3 && !strcmp(argv[1], "export");
  bool enabled = exported || (argc == 3 && !strcmp(argv[1], "implementation"));
  bool produce = argc == 3 && !strcmp(argv[1], "produce");
  if (argc != 3 || (!enabled && !produce && strcmp(argv[1], "native")))
    return 2;
  if (!cacheInfo())
    return 15;
  void *core =
      dlopen("/usr/lib/swift/libswiftCore.dylib", RTLD_NOW | RTLD_LOCAL);
  auto resolve = (Resolve *)dlsym(core, "swift_getTypeByMangledNameInContext");
  auto witness = (Witness *)dlsym(core, "swift_getWitnessTable");
  auto conforms = (Conforms *)dlsym(core, "swift_conformsToProtocol");
  if (!core || !resolve || !witness || !conforms)
    return 3;
  auto type = resolve("s6UInt32VSg", strlen("s6UInt32VSg"), nullptr, nullptr);
  const Word *roots[2] = {(const Word *)dlsym(core, "$ss6UInt32VSQsWP"),
                          (const Word *)dlsym(core, "$ss6UInt32VSHsWP")};
  const char *names[2] = {"$sxSgSQsSQRzlMc", "$sxSgSHsSHRzlMc"};
  if (!type || !roots[0] || !roots[1])
    return 4;
  if (!enabled)
    for (unsigned i = 0; i < 2; i++) {
      auto &e = entries[i];
      e.descriptor = (const Descriptor *)dlsym(core, names[i]);
      e.type = type;
      if (!e.descriptor)
        return 5;
      auto schema = e.descriptor->getGenericWitnessTable();
      if (!schema)
        return 6;
      e.prefix = schema->getWitnessTablePrivateSizeInWords();
      e.original = witness(e.descriptor, type, (const void *const *)&roots[i]);
      if (!e.original)
        return 7;
      e.storage.assign(e.original - e.prefix,
                       e.original + schema->WitnessTableSizeInWords);
      printf("{\"schema\":%u,\"private_words\":%u,\"public_words\":%u,"
             "\"requirements\":%u}\n",
             i, e.prefix, schema->WitnessTableSizeInWords,
             e.descriptor->getProtocol()->NumRequirements);
    }
  if (produce) {
    bool ok = save(argv[2]);
    printf("{\"produced\":%s}\n", ok ? "true" : "false");
    return ok ? 0 : 16;
  }
  if (enabled && !load(argv[2], type))
    return 17;
  unsigned dependencies = 0;
  for (auto &e : entries) {
    unsigned slot = 1;
    for (auto &r : e.descriptor->getProtocol()->getRequirements()) {
      if (r.Flags.getKind() ==
          swift::ProtocolRequirementFlags::Kind::BaseProtocol) {
        for (auto &target : entries)
          if (e.words()[slot] == (Word)target.original ||
              e.words()[slot] == (Word)target.words()) {
            e.words()[slot] = (Word)target.words();
            dependencies++;
            break;
          }
      }
      slot++;
    }
  }
  if (dependencies != 1)
    return 8;
  gum_init_embedded();
  static InlineHook hook;  // inline-hook.h: no GumInterceptor on 27
  bool internal = false;
  implementation = witness_implementation((gpointer)witness, &internal);
  if (!implementation) return 18;
  if (exported) implementation = (gpointer)witness;
  if (enabled) {
    if (!inline_replace(hook, implementation, (gpointer)replacement, (gpointer *)&original)) {
      printf("{\"replace_error\":1}\n");
      return 9;
    }
  }
  unsigned direct = 0, ordinary = 0, adopted = 0;
  const Word *canonical[2] = {nullptr, nullptr};
  for (unsigned i = 0; i < 2; i++) {
    auto &e = entries[i];
    const Word *expected = enabled ? e.words() : e.original;
    for (unsigned repeat = 0; repeat < 2; repeat++) {
      if (witness(e.descriptor, type, (const void *const *)&roots[i]) !=
          expected)
        return 10;
      direct++;
      const Word *found = conforms(type, e.descriptor->getProtocol());
      if (!found || found[0] != (Word)e.descriptor)
        return 11;
      if (repeat && found != canonical[i])
        return 11;
      canonical[i] = found;
      if (found == expected)
        adopted++;
      ordinary++;
    }
  }
  // A non-generic key must still use the runtime's ordinary path.
  auto scalar = resolve("s6UInt32V", strlen("s6UInt32V"), nullptr, nullptr);
  if (witness((const void *)roots[0][0], scalar, nullptr) != roots[0])
    return 12;
  if (enabled)
    inline_revert(hook);
  // Obtain the runtime comparison witnesses only after proving fresh
  // consumption.
  if (enabled)
    for (unsigned i = 0; i < 2; i++)
      entries[i].original =
          witness(entries[i].descriptor, type, (const void *const *)&roots[i]);
  int values = run_witness_values((void *)type);
  if (values != 25)
    return 13;
  if (witness(entries[0].descriptor, type, (const void *const *)&roots[0]) !=
      entries[0].original)
    return 14;
  char uuid[37];
  uuid_unparse_lower(cacheUUID, uuid);
  printf("{\"complete\":true,\"mode\":\"%s\",\"cache_uuid\":\"%s\",\"internal_"
         "implementation\":%s,\"direct_calls\":%u,\"conformance_calls\":%u,"
         "\"conformance_adopted\":%u,\"map_hits\":%u,\"fallbacks\":%u,"
         "\"dependencies\":%u,\"comparisons\":%u,\"hashes\":%u}\n",
         argv[1], uuid, internal ? "true" : "false", direct, ordinary, adopted,
         hits, misses, dependencies, comparisons, hashes);
  gum_deinit_embedded();
  return 0;
}
