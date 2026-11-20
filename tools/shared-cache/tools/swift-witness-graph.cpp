// Record runtime witness constructor keys while the existing recipe helper runs.
// The upstream ABI supplies sizes, conditional keys, and protocol slot kinds.
#include "swift/ABI/Metadata.h"
#include "witness-runtime.h"
#include "inline-hook.h"
#include <cstdio>
#include <dlfcn.h>
#include <map>
#include <mutex>
#include <tuple>
#include <set>
#include <vector>
using Word = uintptr_t;
using Descriptor = swift::ProtocolConformanceDescriptor;
using Witness = const Word *(const Descriptor *, const void *, const void *const *);
struct Observation {
  const Word *witness;
  const Descriptor *descriptor;
  const void *type;
  std::vector<Word> arguments;
  unsigned calls;
  bool derived = false;
  bool canonical = true;
};
static Witness *original;
static gpointer implementation;
static InlineHook witnessHook;
static bool internal;
static std::mutex observationsMutex;
static std::map<std::tuple<const Descriptor *, const void *, const Word *>, Observation> observations;
static unsigned long calls, failed;
using ConformingType = const void *(const void *, const Descriptor *);
static ConformingType *conformingType;
static gboolean conformanceSymbol(const GumSymbolDetails *symbol, gpointer) {
  const char *name="_ZN5swift24findConformingSuperclassEPKNS_14TargetMetadataINS_9InProcessEEEPKNS_35TargetProtocolConformanceDescriptorIS1_EE";
  if (!std::strcmp(symbol->name,name)) conformingType=(ConformingType *)symbol->address;
  return TRUE;
}
// Use the same upstream ABI as the witness decoder. These objects already have
// their canonical identity in an image; copying them creates a second identity.
template <typename T> static bool compilerStatic(const swift::Metadata *metadata) {
  auto value = (const T *)metadata;
  return value->isStaticallySpecializedGenericMetadata() &&
         value->isCanonicalStaticallySpecializedGenericMetadata();
}
extern "C" int swift_graph_compiler_static(const void *pointer) {
  auto metadata = (const swift::Metadata *)pointer;
  if (swift::StructMetadata::classof(metadata))
    return compilerStatic<swift::StructMetadata>(metadata);
  if (swift::EnumMetadata::classof(metadata))
    return compilerStatic<swift::EnumMetadata>(metadata);
  if (swift::ClassMetadata::classof(metadata))
    return compilerStatic<swift::ClassMetadata>(metadata);
  return 0;
}
extern "C" unsigned swift_graph_metadata_kind(const void *pointer) {
  return unsigned(((const swift::Metadata *)pointer)->getKind());
}

// swiftlang/swift caab90f7, lib/Demangling/Demangler.cpp,
// makeSymbolicMangledNameStringRef. Keep the upstream symbolic-reference widths;
// strlen truncates relative references containing zero bytes. The helper does
// not otherwise need to link the compiler's full demangler.
static llvm::StringRef symbolicName(const char *base) {
  if (!base) return {};
  auto end=base;
  while (*end!='\0') {
    if (*end>='\x01' && *end<='\x17') end+=sizeof(uint32_t);
    else if (*end>='\x18' && *end<='\x1F') end+=sizeof(void *);
    ++end;
  }
  return {base,size_t(end-base)};
}

// Remote Mirror supplies concrete type arguments. Complete their generic key
// through the same runtime conformance selection used by ordinary Swift code;
// never reuse witness addresses from the observed application process.
extern "C" const char *swift_graph_generic_arguments(
    const void *pointer, const void **arguments, size_t count, size_t capacity) {
  auto descriptor=(const swift::TypeContextDescriptor *)pointer;
  const auto &header=descriptor->getGenericContextHeader();
  if (header.Flags.hasTypePacks() || header.Flags.hasValues())
    return "generic constructor has pack or value parameters";
  if (header.NumParams!=count || header.NumKeyArguments>capacity)
    return "constructor key differs from type arguments";
  for (auto parameter:descriptor->getGenericParams())
    if (!parameter.hasKeyArgument() || parameter.getKind()!=swift::GenericParamKind::Type)
      return "generic constructor has a non-key, pack or value parameter";
  using Resolve = const void *__attribute__((swiftcall)) (const char *,size_t,const void *,const void *const *);
  using Conforms = const void *__attribute__((swiftcall)) (const void *,const void *);
  auto resolve=(Resolve *)dlsym(RTLD_DEFAULT,"swift_getTypeByMangledNameInContext");
  auto conforms=(Conforms *)dlsym(RTLD_DEFAULT,"swift_conformsToProtocol");
  if (!resolve || !conforms) return "generic requirement runtime API unavailable";
  for (const auto &requirement:descriptor->getGenericContext()->getGenericRequirements()) {
    if (!requirement.Flags.hasKeyArgument()) continue;
    if (count>=header.NumKeyArguments || requirement.Flags.isPackRequirement() ||
        requirement.Flags.isValueRequirement() ||
        requirement.getKind()!=swift::GenericRequirementKind::Protocol)
      return "generic key requirement needs another runtime operation";
    auto protocol=requirement.getProtocol();
    if (protocol.isObjC()) return "generic key requirement is an Objective-C protocol";
    auto subject=symbolicName(requirement.Param.get());
    auto type=resolve(subject.data(),subject.size(),descriptor,arguments);
    auto witness=type?conforms(type,protocol.getSwiftProtocol()):nullptr;
    if (!witness) return "generic protocol requirement did not resolve";
    arguments[count++]=witness;
  }
  return count==header.NumKeyArguments ? nullptr : "generic constructor key width differs";
}
static const Word *record(const Descriptor *descriptor, const void *type,
                          const void *const *arguments) {
  // Copy keys before invoking the original; nested calls get their own frame.
  std::vector<Word> copied;
  for (const auto &requirement : descriptor->getConditionalRequirements())
    if (requirement.Flags.hasKeyArgument())
      copied.push_back(arguments ? (Word)arguments[copied.size()] : 0);
  const Word *result = original(descriptor, type, arguments);
  std::lock_guard<std::mutex> guard(observationsMutex);
  calls++;
  if (!result) { failed++; return result; }
  auto key = std::make_tuple(descriptor, type, result);
  auto found = observations.find(key);
  if (found == observations.end())
    observations.emplace(key, Observation{result, descriptor, type, std::move(copied), 1});
  else
    found->second.calls++;
  return result;
}
extern "C" int swift_witness_graph_start() {
  auto witness = dlsym(RTLD_DEFAULT, "swift_getWitnessTable");
  if (!witness) { std::fprintf(stderr, "witness graph: no swift_getWitnessTable\n"); return 1; }
  gum_init_embedded();
  auto module=gum_process_find_module_by_name("libswiftCore.dylib");
  if (!module) { std::fprintf(stderr, "witness graph: libswiftCore not found\n"); return 2; }
  gum_module_enumerate_symbols(module,conformanceSymbol,nullptr);
  g_object_unref(module);
  if (!conformingType) { std::fprintf(stderr, "witness graph: no conforming-type symbol\n"); return 2; }
  implementation = witness_implementation(witness, &internal);
  if (!implementation) { std::fprintf(stderr, "witness graph: implementation not found\n"); return 2; }
  // inline-hook.h, not GumInterceptor: its unwind broker overflows the stack on 27 x86_64.
  return inline_replace(witnessHook, implementation, (gpointer)record, (gpointer *)&original) ? 0 : 3;
}
static void words(const Word *begin, size_t count) {
  std::putchar('[');
  for (size_t i=0;i<count;i++) std::printf("%s%llu",i?",":"",(unsigned long long)begin[i]);
  std::putchar(']');
}
// Associated requirements are memoized in a live witness table. Preserve the
// image's symbolic initializer: a cached metadata pointer is complete only in
// the capture process, and can bypass singleton initialization during replay.
static Word associatedInitializer(const Descriptor *descriptor,
                                   const swift::ProtocolRequirement &requirement,
                                   unsigned index) {
  auto kind=requirement.Flags.getKind();
  if (kind!=swift::ProtocolRequirementFlags::Kind::AssociatedTypeAccessFunction &&
      kind!=swift::ProtocolRequirementFlags::Kind::AssociatedConformanceAccessFunction)
    return 0;
  auto generic=descriptor->getGenericWitnessTable();
  if (!generic) return 0;
  auto pattern=(const Word *)descriptor->getWitnessTablePattern();
  Word value=pattern && index<generic->WitnessTableSizeInWords ? pattern[index] : 0;
  for (auto &witness:descriptor->getResilientWitnesses())
    if (witness.Requirement.get()==&requirement)
      value=(Word)witness.getWitness(requirement.Flags);
  if (!value) value=(Word)requirement.getDefaultImplementation();
  return value & swift::ProtocolRequirementFlags::AssociatedTypeMangledNameBit ? value : 0;
}
extern "C" void swift_witness_graph_finish(int (*capture)(const Word *, unsigned)) {
  auto native=(Witness *)dlsym(RTLD_DEFAULT,"swift_getWitnessTable");
  using Conforms = const Word *__attribute__((swiftcall)) (const void *,const void *);
  auto conforms=(Conforms *)dlsym(RTLD_DEFAULT,"swift_conformsToProtocol");
  // Ordinary lookup can select another imported conformance or a superclass.
  // Keep its actual constructor in the same closure as direct/base witnesses.
  std::set<const void *> types;
  std::set<std::tuple<const Descriptor *,const void *,const Word *>> visited;
  std::map<std::tuple<const Descriptor *,const void *,const Word *>,const Word *> selections;
  auto remember=[&](const Word *table,const Descriptor *descriptor,const void *type,bool derived) {
    std::vector<Word> arguments;
    for (const auto &conditional:descriptor->getConditionalRequirements())
      if (conditional.Flags.hasKeyArgument()) arguments.push_back(table[-1-(int)arguments.size()]);
    bool canonical=native(descriptor,type,(const void *const *)arguments.data())==table;
    std::lock_guard<std::mutex> guard(observationsMutex);
    auto [entry,inserted]=observations.emplace(std::make_tuple(descriptor,type,table),
        Observation{table,descriptor,type,std::move(arguments),0,derived,canonical});
    if (!inserted) {
      entry->second.derived|=derived;
      entry->second.canonical&=canonical;
    }
  };
  for (;;) {
    std::vector<Observation> pending;
    {
      std::lock_guard<std::mutex> guard(observationsMutex);
      for (const auto &[key,row]:observations)
        if (visited.insert(key).second) pending.push_back(row);
    }
    if (pending.empty()) break;
    for (const auto &row:pending) {
      if (types.insert(row.type).second) capture((const Word *)row.type,0);
      unsigned slot=swift::WitnessTableFirstRequirementOffset;
      for (const auto &req:row.descriptor->getProtocol()->getRequirements()) {
        Word pointer=row.witness[slot++];
        if (req.Flags.getKind()!=swift::ProtocolRequirementFlags::Kind::BaseProtocol || !pointer ||
            (pointer & swift::ProtocolRequirementFlags::AssociatedTypeMangledNameBit)) continue;
        auto table=(const Word *)pointer;
        remember(table,(const Descriptor *)table[0],row.type,true);
      }
      auto selected=conforms(row.type,row.descriptor->getProtocol());
      selections.emplace(std::make_tuple(row.descriptor,row.type,row.witness),selected);
      if (selected) {
        auto descriptor=(const Descriptor *)selected[0];
        auto type=conformingType(row.type,descriptor);
        remember(selected,descriptor,type,false);
      }
    }
  }
  inline_revert(witnessHook);
  // Output is deferred so callbacks cannot interleave partial collector JSON rows.
  std::map<std::tuple<const Descriptor *, const void *, const Word *>, Observation> closed;
  {
    std::lock_guard<std::mutex> guard(observationsMutex);
    closed = observations;
  }
  for (const auto &[key, observation] : closed) {
    (void)key;
    const auto *d = observation.descriptor;
    const auto *g = d->getGenericWitnessTable();
    const auto *protocol = d->getProtocol();
    unsigned prefix = g ? g->getWitnessTablePrivateSizeInWords() : 0;
    unsigned count = protocol->NumRequirements + swift::WitnessTableFirstRequirementOffset;
    std::printf("{\"protocol_witness\":%llu,\"descriptor\":%llu,\"type\":%llu,\"protocol\":%llu,\"private_words\":%u,\"public_words\":%u,\"generic\":%s,\"calls\":%u,\"arguments\":",
       (unsigned long long)observation.witness,(unsigned long long)d,
       (unsigned long long)observation.type,(unsigned long long)protocol,prefix,count,g?"true":"false",observation.calls);
    words(observation.arguments.data(),observation.arguments.size());
    std::printf(",\"words\":");words(observation.witness-prefix,prefix+count);
    std::printf(",\"derived_base\":%s,\"canonical\":%s,\"conditional_requirements\":[",observation.derived?"true":"false",observation.canonical?"true":"false");
    bool first=true;
    for (const auto &r:d->getConditionalRequirements()) {
      std::printf("%s{\"kind\":%u,\"key\":%s,\"pack\":%s,\"value\":%s}",first?"":",",
         unsigned(r.Flags.getKind()),r.Flags.hasKeyArgument()?"true":"false",
         r.Flags.isPackRequirement()?"true":"false",r.Flags.isValueRequirement()?"true":"false");
      first=false;
    }
    std::printf("],\"requirements\":[");first=true;
    unsigned index=swift::WitnessTableFirstRequirementOffset;
    for (const auto &r:protocol->getRequirements()) {
      std::printf("%s{\"kind\":%u,\"flags\":%u,\"data\":%s,\"initializer\":%llu}",first?"":",",
         unsigned(r.Flags.getKind()),r.Flags.getIntValue(),r.Flags.isData()?"true":"false",
         (unsigned long long)associatedInitializer(d,r,index++));first=false;
    }
    std::puts("]}");
  }
  for (const auto &[key,selected]:selections) {
    auto [descriptor,type,table]=key;
    std::printf("{\"conformance_lookup\":%llu,\"descriptor\":%llu,\"type\":%llu,\"selected\":%llu}\n",
       (unsigned long long)table,(unsigned long long)descriptor,(unsigned long long)type,(unsigned long long)selected);
  }
  std::printf("{\"witness_graph\":true,\"internal_implementation\":%s,\"calls\":%lu,\"failed\":%lu,\"records\":%zu}\n",internal?"true":"false",calls,failed,closed.size());
  // Other library threads may still exist; Gum's process resources live until exit.
}

// Ordinary metatypes have one instance-type edge and the runtime's immutable
// pointer value-witness table. They have no generic completion state. Export a
// zero padding word before the value-witness header, matching the catalog's
// address point; never copy the adjacent process-local cache-entry storage.
extern "C" int swift_graph_capture_metatype(
    const void *pointer, unsigned depth, int (*capture)(const Word *, unsigned),
    int (*cached)(Word)) {
  auto metadata = (const swift::Metadata *)pointer;
  if (!swift::MetatypeMetadata::classof(metadata)) return -1;
  auto value = (const swift::MetatypeMetadata *)metadata;
  using Constructor = const swift::Metadata *(const swift::Metadata *);
  auto constructor = (Constructor *)dlsym(RTLD_DEFAULT, "swift_getMetatypeMetadata");
  auto instance = value->InstanceType;
  auto witnesses = metadata->getValueWitnesses();
  const char *error = nullptr;
  if (!constructor || !cached((Word)constructor)) error = "metatype constructor is not image-backed";
  else if (!cached((Word)witnesses)) error = "metatype value witnesses are not image-backed";
  else if (constructor(instance) != metadata) error = "noncanonical metatype metadata";
  else if (!cached((Word)instance) && !capture((const Word *)instance, depth+1)) {
    std::printf("{\"node\":%llu,\"status\":\"unsupported\",\"reason\":\"unsupported metatype instance dependency\",\"dependency\":%llu}\n",
                (unsigned long long)pointer, (unsigned long long)instance);
    return 0;
  }
  struct Name { const char *data; size_t length; };
  using GetName = Name __attribute__((swiftcall)) (const void *);
  auto getName = (GetName *)dlsym(RTLD_DEFAULT, "swift_getMangledTypeName");
  Name name = error || !getName ? Name{nullptr,0} : getName(metadata);
  if (!error && (!name.data || !name.length)) error = "metatype name unavailable";
  Dl_info image = {};
  if (!error && !dladdr((const void *)constructor, &image)) error = "metatype constructor owner unavailable";
  if (error) {
    std::printf("{\"node\":%llu,\"status\":\"unsupported\",\"reason\":\"%s\",\"metadata_kind\":%u}\n",
                (unsigned long long)pointer,error,unsigned(metadata->getKind()));
    return 0;
  }
  auto string = [](const char *bytes, size_t size) {
    std::putchar('"');
    for (size_t i=0;i<size;i++) {
      auto c=(unsigned char)bytes[i];
      if (c=='"' || c=='\\') std::printf("\\%c",c);
      else if (c<32) std::printf("\\u%04x",c);
      else std::putchar(c);
    }
    std::putchar('"');
  };
  static_assert(sizeof(swift::MetatypeMetadata) == 2*sizeof(Word), "unsupported metatype ABI extent");
  Word payload[] = {0,(Word)witnesses,(Word)metadata->getKind(),(Word)instance};
  Word key[] = {(Word)constructor,(Word)instance};
  std::printf("{\"node\":%llu,\"status\":\"captured\",\"abi_family\":\"metatype\",\"canonical\":true,\"metadata_kind\":%u,\"name\":",
              (unsigned long long)pointer,unsigned(metadata->getKind()));
  string(name.data,name.length); std::printf(",\"owner\":");
  string(image.dli_fname,std::strlen(image.dli_fname));
  std::printf(",\"key\":"); words(key,2);
  std::printf(",\"full_metadata\":"); words(payload,4);
  std::puts("}");
  return 1;
}
