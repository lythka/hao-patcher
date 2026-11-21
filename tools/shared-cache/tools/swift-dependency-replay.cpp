// Replay generated keys through either isolated maps or the installed cache.
// The installed build never maps replacement records or installs interceptors.
#ifdef SWIFT_DEPENDENCY_INSTALLED
#include "frida-gum.h"
#else
#include "witness-runtime.h"
#include "inline-hook.h"
#endif
#include "swift/ABI/Metadata.h"
#ifdef SWIFT_WITNESS_RUNTIME
extern "C" {
#include "swift-witness-runtime.h"
}
static uintptr_t witness_offsets[3];
#define WITNESS_ONCE_OFFSET witness_offsets[0]
#define WITNESS_INITIALIZE_OFFSET witness_offsets[1]
#define WITNESS_MODE_OFFSET witness_offsets[2]
#endif
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <execinfo.h>
#include <fstream>
#include <map>
#include <signal.h>
#include <set>
#include <string>
#include <sys/mman.h>
#include <unistd.h>
#include <uuid/uuid.h>
#include <vector>
using Word = uintptr_t;
struct Response { const void *value; Word state; };
typedef Response __attribute__((swiftcall)) Generic(Word, const void *const *, const swift::TypeContextDescriptor *);
typedef const void *LookupMetadata(const swift::TypeContextDescriptor *,const void *const *);
typedef const Word *Witness(const void *, const void *, const void *const *);
typedef const Word *__attribute__((swiftcall)) Conforms(const void *, const void *);
struct Name { const char *data; size_t length; };
typedef Name __attribute__((swiftcall)) GetName(const void *);
typedef const void *__attribute__((swiftcall)) Resolve(const char *,size_t,const void *,const void *const *);
typedef const swift::TypeContextDescriptor *__attribute__((swiftcall)) GetContext(const void *);
typedef Response __attribute__((swiftcall)) Access(Word);
typedef Response __attribute__((swiftcall)) CheckState(Word,const void *);
typedef Response __attribute__((swiftcall)) AssociatedType(Word,const Word *,const void *,
    const swift::ProtocolRequirement *,const swift::ProtocolRequirement *);
using ConformingType = const void *(const void *,const swift::ProtocolConformanceDescriptor *);
static ConformingType *conformingType;
typedef int32_t GenericOffset(const swift::TypeContextDescriptor *);
struct SymbolAddress { gpointer address; unsigned count; };
struct Row { Word kind, value; std::vector<Word> key; };
#ifndef SWIFT_DEPENDENCY_INSTALLED
static std::map<std::vector<Word>, Word> metadata, witnesses;
static LookupMetadata *originalMetadata;
static Witness *originalWitness;
using Metatype = const void *(const void *);
static Metatype *originalMetatype;
#endif
static std::atomic<unsigned> metadataHits, witnessHits, metadataMisses, witnessMisses;
static Generic *generic;
static const void *(*metatype)(const void *);
static GetContext *getContext;
static CheckState *checkState;
// REPLAY_CONTINUE=1 (diagnostic): log a record whose initialization changes identity and go on, so one run lists them
// all. Never set by metadata-expand; the verdict still fails on any such record.
static bool keepGoing;
static GenericOffset *genericOffset;
static unsigned genericOffsetSymbols;
static std::set<Word> initialized,initializing;
static bool initialize(const void *type) {
  Word identity=(Word)type;
  if (initialized.count(identity)) return true;
  if (!initializing.insert(identity).second) return false;
  auto descriptor=getContext(type);
  auto metadata=(const swift::Metadata *)type;
  bool canonicalStatic=false;
  if (descriptor && swift::StructMetadata::classof(metadata))
    canonicalStatic=((const swift::StructMetadata *)metadata)->isCanonicalStaticallySpecializedGenericMetadata();
  else if (descriptor && swift::EnumMetadata::classof(metadata))
    canonicalStatic=((const swift::EnumMetadata *)metadata)->isCanonicalStaticallySpecializedGenericMetadata();
  else if (descriptor && swift::ClassMetadata::classof(metadata))
    canonicalStatic=((const swift::ClassMetadata *)metadata)->isCanonicalStaticallySpecializedGenericMetadata();
  if (canonicalStatic) { initializing.erase(identity); initialized.insert(identity); return true; }
  Response response={type,0};
  if (swift::MetatypeMetadata::classof(metadata)) {
    auto instance=((const swift::MetatypeMetadata *)metadata)->InstanceType;
    if (!initialize(instance)) return false;
    response.value=metatype(instance);
  } else if (descriptor && descriptor->isGeneric()) {
    auto arguments=(const void *const *)type+genericOffset(descriptor);
    unsigned index=0;
    for (auto &parameter:descriptor->getGenericParams())
      if (parameter.hasKeyArgument() && !initialize(arguments[index++])) return false;
    response=generic(0,arguments,descriptor);
  } else if (descriptor && descriptor->AccessFunctionPtr &&
      (descriptor->hasSingletonMetadataInitialization() ||
       descriptor->hasForeignMetadataInitialization())) {
    // Exact-key replay bypasses name resolution, which normally initializes
    // lazy singleton dependencies before they reach a generic constructor.
    auto accessor=(Access *)(void *)descriptor->AccessFunctionPtr.get();
    response=accessor(0);
  }
  bool foreignAlias=false;
  if (descriptor && descriptor->hasForeignMetadataInitialization() &&
      response.value!=type && !response.state) {
    // ForeignMetadataCache uniques duplicate image templates by type context.
    // Both the accessor and the state lookup must identify the same complete
    // canonical object. Catalog record identities are still checked below.
    auto state=checkState(0,type);
    foreignAlias=state.value==response.value && !state.state;
  }
  if ((response.value!=type && !foreignAlias) || response.state) {
    fprintf(stderr,"metadata initialization changed identity %p -> %p (state %zu)\n",type,response.value,response.state);
    return false;
  }
  initializing.erase(identity); initialized.insert(identity); return true;
}
#ifndef SWIFT_DEPENDENCY_INSTALLED
static const void *replaceMetadata(
    const swift::TypeContextDescriptor *descriptor,const void *const *arguments) {
  std::vector<Word> key{(Word)descriptor};
  if (descriptor->isGeneric()) {
    auto count = descriptor->getGenericContextHeader().NumKeyArguments;
    for (unsigned i=0; i<count; i++) key.push_back((Word)arguments[i]);
  }
  auto found=metadata.find(key);
  if (found!=metadata.end()) { metadataHits++; return (void *)found->second; }
  metadataMisses++;
  return originalMetadata(descriptor,arguments);
}
static const void *replaceMetatype(const void *instance) {
#ifdef SWIFT_WITNESS_RUNTIME
  return hao_metatype_lookup(instance);
#else
  auto found=metadata.find({(Word)metatype,(Word)instance});
  if (found!=metadata.end()) { metadataHits++; return (void *)found->second; }
  metadataMisses++;
  return originalMetatype(instance);
#endif
}
static const Word *replaceWitness(const void *descriptor, const void *type, const void *const *arguments) {
#ifdef SWIFT_WITNESS_RUNTIME
  return (const Word *)hao_witness_lookup(descriptor,type,arguments);
#else
  auto found=witnesses.find({(Word)descriptor,(Word)type});
  if (found!=witnesses.end()) { witnessHits++; return (const Word *)found->second; }
  witnessMisses++;
  return originalWitness(descriptor,type,arguments);
#endif
}
#ifdef SWIFT_WITNESS_RUNTIME
static const void *runtimeLookup(const void *table,const void *key,size_t count,const void **rest) {
  if (count!=1) return nullptr;
  auto &entries=table==&metadata ? metadata : witnesses;
  auto found=entries.find({(Word)key,(Word)rest[0]});
  if (found!=entries.end()) { (table==&metadata ? metadataHits : witnessHits)++; return (const void *)found->second; }
  return nullptr;
}
static const void *runtimeMetatypeFallback(const void *instance) {
  metadataMisses++; return originalMetatype(instance);
}
static const void *runtimeFallback(const void *descriptor,const void *type,const void *const *arguments) {
  witnessMisses++; return originalWitness(descriptor,type,arguments);
}
#endif
#endif
static gboolean metadataSymbol(const GumSymbolDetails *symbol, gpointer opaque) {
  auto result=static_cast<SymbolAddress *>(opaque);
  if (!strcmp(symbol->name,"_ZN5swift24findConformingSuperclassEPKNS_14TargetMetadataINS_9InProcessEEEPKNS_35TargetProtocolConformanceDescriptorIS1_EE")) conformingType=(ConformingType *)symbol->address;
  // Intercept the precomputed-data lookup, not swift_getGenericMetadata. The
  // latter must still register every returned type in its metadata-state cache.
  const char *name="_ZN5swift28getLibPrespecializedMetadataEPKNS_27TargetTypeContextDescriptorINS_9InProcessEEEPKPKv";
  if (!strcmp(symbol->name,name)) { result->address=(gpointer)symbol->address; result->count++; }
  const char *offset="_ZNK5swift27TargetTypeContextDescriptorINS_9InProcessEE24getGenericArgumentOffsetEv";
  if (!strcmp(symbol->name,offset)) { genericOffset=(GenericOffset *)symbol->address; genericOffsetSymbols++; }
  return TRUE;
}
static void readBytes(FILE *f, void *dest, size_t size) {
  if (fread(dest,1,size,f)!=size) { fprintf(stderr,"truncated dependency replay input\n"); exit(3); }
}
static Word readWord(FILE *f) { Word value; readBytes(f,&value,sizeof(value)); return value; }
static void fault(int signal) {
  void *frames[64]; int count=backtrace(frames,64);
  backtrace_symbols_fd(frames,count,STDERR_FILENO);
  _exit(128+signal);
}
int main(int argc,char **argv) {
  if (argc!=3) return 2;
#ifdef SWIFT_WITNESS_RUNTIME
  std::string profilePath(argv[1]);
  auto slash=profilePath.find_last_of('/');
  profilePath=(slash==std::string::npos?std::string():profilePath.substr(0,slash+1))+"runtime-profile.bin";
  FILE *profile=fopen(profilePath.c_str(),"rb");
  if (!profile || fread(witness_offsets,1,sizeof(witness_offsets),profile)!=sizeof(witness_offsets) || fgetc(profile)!=EOF) return 3;
  fclose(profile);
#endif
  keepGoing=getenv("REPLAY_CONTINUE")!=nullptr;
  alarm(keepGoing?900:90);
  setbuf(stdout,nullptr);
  // Preserve the failing native call path even when this dev image does not
  // save a crash report for a short-lived root helper.
  struct sigaction action={}; action.sa_handler=fault; action.sa_flags=SA_RESETHAND;
  for (int signal:{SIGSEGV,SIGBUS,SIGABRT}) sigaction(signal,&action,nullptr);
  auto cacheRange=(const void *(*)(size_t *))dlsym(RTLD_DEFAULT,"_dyld_get_shared_cache_range");
  auto cacheUUID=(bool (*)(uuid_t))dlsym(RTLD_DEFAULT,"_dyld_get_shared_cache_uuid");
  uuid_t uuid,expected;
  size_t cacheSize=0;
  if (!cacheRange || !cacheUUID || !cacheUUID(uuid)) return 4;
  Word base=(Word)cacheRange(&cacheSize);
  FILE *file=fopen(argv[1],"rb"); if (!file) return 5;
  char magic[8]; readBytes(file,magic,8); readBytes(file,expected,16);
#ifdef SWIFT_DEPENDENCY_INSTALLED
  constexpr bool installed=true;
  const char *expectedMagic="SWINS001";
#else
  constexpr bool installed=false;
  const char *expectedMagic="SWDEP001";
#endif
  if (memcmp(magic,expectedMagic,8) || memcmp(uuid,expected,16)) return 6;
  Word oldBase=readWord(file),oldAddress=readWord(file),size=readWord(file);
  Word pointerCount=readWord(file),recordCount=readWord(file);
#ifdef SWIFT_DEPENDENCY_INSTALLED
  if (size || pointerCount || oldAddress) return 8;
  auto rebase=[&](Word pointer) { return base+pointer-oldBase; };
#else
  auto arena=(unsigned char *)mmap(nullptr,size,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);
  if (arena==MAP_FAILED) return 7;
  readBytes(file,arena,size);
  auto rebase=[&](Word pointer) {
    if (pointer>=oldAddress && pointer-oldAddress<size) return (Word)arena+pointer-oldAddress;
    return base+pointer-oldBase;
  };
  for (Word i=0;i<pointerCount;i++) {
    Word field=readWord(file),target=rebase(readWord(file));
    if (field<oldAddress || field-oldAddress>size-8) return 8;
    // Four-byte-aligned layout-program pointer fields are legal on x86_64.
    memcpy(arena+field-oldAddress,&target,8);
  }
#endif
  std::vector<Row> records;
  for (Word i=0;i<recordCount;i++) {
    Row row; row.kind=readWord(file); Word count=readWord(file); row.value=rebase(readWord(file));
    for (Word j=0;j<count;j++) row.key.push_back(rebase(readWord(file)));
#ifndef SWIFT_DEPENDENCY_INSTALLED
    (row.kind==1 ? witnesses : metadata).emplace(row.key,row.value);
#endif
    records.push_back(row);
  }
  fclose(file);
  auto core=dlopen("/usr/lib/swift/libswiftCore.dylib",RTLD_NOW|RTLD_LOCAL);
  generic=(Generic *)dlsym(core,"swift_getGenericMetadata");
  getContext=(GetContext *)dlsym(core,"swift_getTypeContextDescriptor");
  checkState=(CheckState *)dlsym(core,"swift_checkMetadataState");
  metatype=(decltype(metatype))dlsym(core,"swift_getMetatypeMetadata");
  auto witness=(Witness *)dlsym(core,"swift_getWitnessTable");
  auto conforms=(Conforms *)dlsym(core,"swift_conformsToProtocol");
  auto name=(GetName *)dlsym(core,"swift_getMangledTypeName");
  auto resolve=(Resolve *)dlsym(core,"swift_getTypeByMangledNameInContext");
  if (!resolve || !metatype || !generic || !getContext || !checkState || !witness || !conforms || !name) return 10;
  gum_init_embedded();
#ifndef SWIFT_DEPENDENCY_INSTALLED
  static InlineHook metatypeHook,witnessHook,metadataHook;  // inline-hook.h: no GumInterceptor on 27
  bool internalWitness=false;
  auto witnessImpl=witness_implementation((gpointer)witness,&internalWitness);
#endif
  auto module=gum_process_find_module_by_name("libswiftCore.dylib");
  SymbolAddress metadataImpl={};
  gum_module_enumerate_symbols(module,metadataSymbol,&metadataImpl); g_object_unref(module);
  if (genericOffsetSymbols!=1 || !conformingType) return 11;
#ifndef SWIFT_DEPENDENCY_INSTALLED
  if (!internalWitness || metadataImpl.count!=1) return 11;
#ifdef SWIFT_WITNESS_RUNTIME
  hao_witness_runtime={(const intptr_t *)(base+WITNESS_ONCE_OFFSET),(void (*)(void))(base+WITNESS_INITIALIZE_OFFSET),
      (const uint32_t *)(base+WITNESS_MODE_OFFSET),&witnesses,runtimeLookup,runtimeFallback};
  hao_metatype_runtime={(const intptr_t *)(base+WITNESS_ONCE_OFFSET),(void (*)(void))(base+WITNESS_INITIALIZE_OFFSET),
      (const uint32_t *)(base+WITNESS_MODE_OFFSET),&metadata,runtimeLookup,(void *)metatype,runtimeMetatypeFallback};
#endif
  bool hasMetatypes=false;
  for (auto &row:records) if(row.kind==2) hasMetatypes=true;
  if (hasMetatypes && !inline_replace(metatypeHook,(gpointer)metatype,(gpointer)replaceMetatype,
      (gpointer *)&originalMetatype)) return 16;
  if (!inline_replace(witnessHook,witnessImpl,(gpointer)replaceWitness,(gpointer *)&originalWitness)) return 12;
  if (!inline_replace(metadataHook,metadataImpl.address,(gpointer)replaceMetadata,
      (gpointer *)&originalMetadata)) return 13;
#endif
  // Framework initializers can request metadata. Register the candidate first
  // so those requests cannot establish competing process-local identities.
  std::ifstream loads(argv[2]); std::string path;
  while (std::getline(loads,path)) if (!path.empty() && !dlopen(path.c_str(),RTLD_LAZY|RTLD_LOCAL)) {
    fprintf(stderr,"load %s: %s\n",path.c_str(),dlerror()); return 9;
  }
  unsigned directMetadata=0,directWitness=0,ordinary=0,adopted=0,named=0,failures=0;
  unsigned ordinaryFallbacks=0,ordinaryVariants=0,ordinaryValidated=0;
  std::map<std::vector<Word>,size_t> expectedWitnesses;
  for (size_t index=0;index<records.size();index++)
    if (records[index].kind==1) expectedWitnesses.emplace(records[index].key,index);
  unsigned metatypes=0,metatypeResolved=0,metatypeValues=0;
  auto associatedType=(AssociatedType *)dlsym(core,"swift_getAssociatedTypeWitness");
  if (!associatedType) return 18;
  unsigned associatedRequests=0,associatedValidated=0;
  // Exercise associated types before the metadata loop warms every dependency.
  // A captured completion memo can otherwise pass identity replay yet crash a
  // real app's first metadata-state check (PhotosSectionPickerContext? on r14).
  for (size_t i=0;i<records.size();i++) if (records[i].kind==1) {
    auto &row=records[i];
    if (!initialize((void *)row.key[1])) {
      if (!keepGoing) return 15;
      printf("{\"identity_failure\":%zu,\"phase\":\"associated\"}\n",i); continue;
    }
    auto descriptor=(const swift::ProtocolConformanceDescriptor *)row.key[0];
    auto table=(const Word *)row.value;
    std::vector<const void *> arguments;
    for (const auto &requirement:descriptor->getConditionalRequirements())
      if (requirement.Flags.hasKeyArgument()) arguments.push_back((const void *)table[-1-(int)arguments.size()]);
    auto found=witness(descriptor,(const void *)row.key[1],arguments.data());
    if (found!=table) return 18;
    auto requirements=descriptor->getProtocol()->getRequirements();
    auto base=requirements.data()-swift::WitnessTableFirstRequirementOffset;
    for (const auto &requirement:requirements) {
      if (requirement.Flags.getKind()!=swift::ProtocolRequirementFlags::Kind::AssociatedTypeAccessFunction) continue;
      printf("{\"associated_begin\":%zu,\"slot\":%zu}\n",i,(size_t)(&requirement-base));
      auto response=associatedType(0,table,(const void *)row.key[1],base,&requirement);
      associatedRequests++;
      if (!response.value || response.state) return 18;
      auto state=checkState(0,response.value);
      if (state.value!=response.value || state.state) return 18;
      associatedValidated++;
    }
  }
  printf("{\"associated_summary\":true,\"before_metadata_replay\":true,\"requests\":%u,\"validated\":%u}\n",
         associatedRequests,associatedValidated);
  for (size_t i=0;i<records.size();i++) {
    auto &row=records[i]; bool direct=false,canonical=false;
    const char *ordinarySource="invalid";
    size_t ordinaryRecord=records.size();
    bool ordinaryCached=false;
    printf("{\"begin\":%zu,\"kind\":%zu}\n",i,row.kind);
    if (row.kind!=1) {
      if (!initialize((void *)row.value)) {
        if (!keepGoing) return 15;
        printf("{\"identity_failure\":%zu,\"phase\":\"metadata\"}\n{\"record\":%zu,\"direct\":false}\n",i,i);
        failures++; continue;
      }
      if (row.kind==2) {
        if(row.key.size()!=2 || row.key[0]!=(Word)metatype) return 14;
        direct=metatype((const void *)row.key[1])==(const void *)row.value;
      } else {
        auto descriptor=(const swift::TypeContextDescriptor *)row.key[0];
        if (!descriptor->isGeneric() || descriptor->getGenericContextHeader().NumKeyArguments+1!=row.key.size()) return 14;
        auto response=generic(0,(const void *const *)(row.key.data()+1),descriptor);
        direct=response.value==(const void *)row.value && response.state==0;
      }
      directMetadata+=direct;
      auto typeName=name((void *)row.value);
      if (typeName.data && typeName.length) named++; else failures++;
      canonical=true;
      if(row.kind==2) {
        metatypes++;
        bool resolved=typeName.data && resolve(typeName.data,typeName.length,nullptr,nullptr)==(void *)row.value;
        metatypeResolved+=resolved;
        auto type=(const swift::Metadata *)row.value;
        auto values=type->getValueWitnesses();
        Word value=row.key[1],copy=0;
        if(values->getSize()!=sizeof(Word)) return 17;
        values->initializeWithCopy((swift::OpaqueValue *)&copy,(swift::OpaqueValue *)&value,type);
        bool copied=copy==value;
        values->destroy((swift::OpaqueValue *)&copy,type);
        metatypeValues+=copied;
        if(!resolved || !copied) failures++;
      }
    } else {
      if (!initialize((void *)row.key[1])) {
        if (!keepGoing) return 15;
        printf("{\"identity_failure\":%zu,\"phase\":\"witness\"}\n{\"record\":%zu,\"direct\":false}\n",i,i);
        failures++; continue;
      }
      auto descriptor=(const swift::ProtocolConformanceDescriptor *)row.key[0];
      auto table=(const Word *)row.value;
      std::vector<const void *> arguments;
      unsigned index=0;
      for (auto &requirement:descriptor->getConditionalRequirements())
        if (requirement.Flags.hasKeyArgument()) arguments.push_back((const void *)table[-1-static_cast<int>(index++)]);
      direct=witness(descriptor,(const void *)row.key[1],arguments.data())==table;
      directWitness+=direct;
      printf("{\"witness_direct\":%zu,\"matched\":%s}\n",i,direct?"true":"false");
      auto found=conforms((const void *)row.key[1],descriptor->getProtocol());
      ordinary++;
      canonical=false;
      if (found && ((const swift::ProtocolConformanceDescriptor *)found[0])->getProtocol()==descriptor->getProtocol()) {
        // Native ordinary lookup may select an imported duplicate or inherited
        // conformance. Check that constructor's exact catalog identity when
        // present; a validated runtime-only result is still a coverage miss.
        auto selectedDescriptor=(const swift::ProtocolConformanceDescriptor *)found[0];
        auto selectedType=conformingType((const void *)row.key[1],selectedDescriptor);
        auto expected=expectedWitnesses.find({(Word)selectedDescriptor,(Word)selectedType});
        if (expected!=expectedWitnesses.end()) {
          ordinarySource="catalog";
          ordinaryRecord=expected->second;
          canonical=(Word)found==records[ordinaryRecord].value;
          ordinaryCached=canonical;
          adopted+=canonical;
          ordinaryVariants+=canonical && records[ordinaryRecord].key!=row.key;
        } else {
          ordinarySource="runtime";
          std::vector<const void *> selectedArguments;
          for (const auto &requirement:selectedDescriptor->getConditionalRequirements())
            if (requirement.Flags.hasKeyArgument()) selectedArguments.push_back((const void *)found[-1-(int)selectedArguments.size()]);
          canonical=witness(selectedDescriptor,selectedType,selectedArguments.data())==found;
          ordinaryFallbacks+=canonical;
        }
      }
      ordinaryValidated+=canonical;
    }
    if (!direct || !canonical) failures++;
    printf("{\"record\":%zu,\"direct\":%s,\"ordinary\":%s",i,direct?"true":"false",row.kind==1?(ordinaryCached?"true":"false"):"null");
    if (row.kind==1) {
      printf(",\"ordinary_valid\":%s,\"ordinary_source\":\"%s\",\"ordinary_record\":",canonical?"true":"false",ordinarySource);
      if (ordinaryRecord<records.size()) printf("%zu",ordinaryRecord); else printf("null");
    }
    puts("}");
  }
  bool policyDisabled=false, runtimePolicy=false;
#ifdef SWIFT_WITNESS_RUNTIME
  runtimePolicy=true;
  bool witnessPolicyPassed=true,metatypePolicyPassed=true,policyTested=false;
  // Only this helper's COW runtime state is changed. A disabled native mode
  // must skip the generated map while preserving constructor arguments.
  for (auto &row:records) if (row.kind==1) {
    auto descriptor=(const swift::ProtocolConformanceDescriptor *)row.key[0];
    auto table=(const Word *)row.value; std::vector<const void *> arguments; unsigned index=0;
    for (auto &requirement:descriptor->getConditionalRequirements())
      if (requirement.Flags.hasKeyArgument()) arguments.push_back((const void *)table[-1-static_cast<int>(index++)]);
    auto mode=const_cast<uint32_t *>(hao_witness_runtime.mode);
    auto saved=__atomic_load_n(mode,__ATOMIC_RELAXED);
    auto hits=witnessHits.load(),misses=witnessMisses.load();
    __atomic_store_n(mode,4,__ATOMIC_RELAXED);
    auto found=witness(descriptor,(const void *)row.key[1],arguments.data());
    __atomic_store_n(mode,saved,__ATOMIC_RELAXED);
    witnessPolicyPassed=found && found[0]==row.key[0] && witnessHits.load()==hits && witnessMisses.load()>misses;
    policyTested=true;
    if (!witnessPolicyPassed) failures++;
    break;
  }
  for (auto &row:records) if (row.kind==2) {
    auto mode=const_cast<uint32_t *>(hao_metatype_runtime.mode);
    auto saved=__atomic_load_n(mode,__ATOMIC_RELAXED);
    auto hits=metadataHits.load(),misses=metadataMisses.load();
    __atomic_store_n(mode,4,__ATOMIC_RELAXED);
    auto found=(const Word *)metatype((const void *)row.key[1]);
    __atomic_store_n(mode,saved,__ATOMIC_RELAXED);
    metatypePolicyPassed=found && found[0]==0x304 && found[1]==row.key[1] &&
                        metadataHits.load()==hits && metadataMisses.load()>misses;
    policyTested=true;
    if (!metatypePolicyPassed) failures++;
    break;
  }
  policyDisabled=policyTested && witnessPolicyPassed && metatypePolicyPassed;
#endif
  printf("{\"metatype_summary\":true,\"requests\":%u,\"resolved\":%u,\"value_operations\":%u}\n",metatypes,metatypeResolved,metatypeValues);
  printf("{\"complete\":true,\"metadata_requests\":%u,\"metadata_names\":%u,\"witness_requests\":%u,"
         "\"ordinary_requests\":%u,\"ordinary_adopted\":%u,\"ordinary_validated\":%u,\"ordinary_fallbacks\":%u,\"ordinary_variants\":%u,\"metadata_hits\":%u,\"witness_hits\":%u,"
         "\"installed\":%s,\"metadata_fallbacks\":%u,\"witness_fallbacks\":%u,\"runtime_policy\":%s,\"policy_disabled_fallback\":%s,\"failures\":%u}\n",
         directMetadata,named,directWitness,ordinary,adopted,ordinaryValidated,ordinaryFallbacks,ordinaryVariants,metadataHits.load(),witnessHits.load(),installed?"true":"false",metadataMisses.load(),witnessMisses.load(),runtimePolicy?"true":"false",policyDisabled?"true":"false",failures);
  // Keep hooks and backing storage alive until process exit: Swift may retain
  // these canonical identities in its own caches after a successful lookup.
  return failures?1:0;
}
