// Quiet native26A428 E5 policy, derived from trace_tensor_input.cpp and
// trace_allocation.mm. Only TextRecognition compile paths select GPU or redirect.
// 26B131: every e5rt_* function used here is in x86_64 Espresso (dyld_shared_cache_x86_64, 27.2.1).
#import <Foundation/Foundation.h>
#import <IOSurface/IOSurface.h>
#import <objc/runtime.h>
#import <objc/message.h>
#include <dlfcn.h>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <string>
#include <unistd.h>
#include <mutex>
#include <atomic>
extern "C" {
void hao_live_text_record_library(void*,int) __attribute__((weak_import));
int e5rt_e5_compiler_compile(void*,void*,void*,void*) __attribute__((weak_import));
int e5rt_e5_compiler_options_get_compute_device_types_mask(void*,uint64_t*) __attribute__((weak_import));
int e5rt_e5_compiler_options_set_compute_device_types_mask(void*,uint64_t) __attribute__((weak_import));
int e5rt_e5_compiler_create(void**) __attribute__((weak_import));
int e5rt_e5_compiler_create_with_config(void**,void*) __attribute__((weak_import));
int e5rt_e5_compiler_config_options_create(void**) __attribute__((weak_import));
int e5rt_e5_compiler_config_options_set_cache_bundle_location(void*,const char*) __attribute__((weak_import));
int e5rt_e5_compiler_config_options_release(void**) __attribute__((weak_import));
}
static const char *root(){const char *p=getenv("HAO_LIVE_TEXT_ROOT");return p&&*p&&strcmp(p,"0")?p:nullptr;}
static bool nonmemref_ready=false;
static std::atomic<bool> recognition_active{false};
extern "C" bool hao_live_text_recognition_active(){return recognition_active.load(std::memory_order_acquire);}
typedef id __attribute__((ns_returns_retained)) (*InitFunction)(id __attribute__((ns_consumed)),SEL);
static InitFunction original_init;
static signed char (*original_getter)(id,SEL);
static signed char nonmemref(id self,SEL selector){return root()?0:original_getter(self,selector);}
static id __attribute__((ns_returns_retained)) descriptor_init(id __attribute__((ns_consumed)) self,SEL selector){
 id result=original_init(self,selector);
 if(result&&root())reinterpret_cast<void(*)(id,SEL,signed char)>(objc_msgSend)(result,sel_registerName("setEnableMemrefBufferization:"),0);
 return result;
}
static bool type_is(Method m,unsigned i,const char *want){char *s=method_copyArgumentType(m,i);bool ok=s&&!strcmp(s,want);free(s);return ok;}
static bool signature(Method m,const char *result,unsigned count,const char *last=nullptr){
 if(!m||method_getNumberOfArguments(m)!=count)return false;char *s=method_copyReturnType(m);bool ok=s&&!strcmp(s,result);free(s);
 return ok&&type_is(m,0,"@")&&type_is(m,1,":")&&(!last||type_is(m,count-1,last));
}
static void replace_local(Class cls,SEL selector,Method old,IMP replacement){
 // Do not replace an inherited NSObject method globally.
 if(!class_addMethod(cls,selector,replacement,method_getTypeEncoding(old)))method_setImplementation(class_getInstanceMethod(cls,selector),replacement);
}
static void install_nonmemref(){
 if(!root())return;
 dlopen("/System/Library/Frameworks/MetalPerformanceShadersGraph.framework/MetalPerformanceShadersGraph",RTLD_LAZY|RTLD_LOCAL);
 Class cls=objc_getClass("MPSGraphCompilationDescriptor");SEL get=sel_registerName("enableMemrefBufferization"),set=sel_registerName("setEnableMemrefBufferization:"),init=sel_registerName("init");
 Method gm=class_getInstanceMethod(cls,get),sm=class_getInstanceMethod(cls,set),im=class_getInstanceMethod(cls,init);
 if(!signature(gm,"c",2)||!signature(sm,"v",3,"c")||!signature(im,"@",2))return;
 original_getter=reinterpret_cast<signed char(*)(id,SEL)>(method_getImplementation(gm));original_init=reinterpret_cast<InitFunction>(method_getImplementation(im));
 replace_local(cls,get,gm,reinterpret_cast<IMP>(nonmemref));replace_local(cls,init,im,reinterpret_cast<IMP>(descriptor_init));nonmemref_ready=true;
}
static bool text_model(const char *path){return path&&strstr(path,"/TextRecognition.framework/");}
static bool detector_model(const char *path){
 if(!text_model(path))return false;
 const char suffix[]="/cr_td_model_v3_e5.mlmodelc.bundle/model.mil";size_t n=strlen(path),s=sizeof(suffix)-1;return n>=s&&!strcmp(path+n-s,suffix);
}
struct MaskRestore {void *options;uint64_t previous;bool restored=false;~MaskRestore(){if(!restored)e5rt_e5_compiler_options_set_compute_device_types_mask(options,previous);}int restore(){restored=true;return e5rt_e5_compiler_options_set_compute_device_types_mask(options,previous);}};
extern "C" int live_text_compile(void *compiler,void *model,void *options,void *out){
 const char *path=static_cast<const char*>(model),*package=root();if(!package||!text_model(path))return e5rt_e5_compiler_compile(compiler,model,options,out);
 // Loading Metal alone must not initialize MPSGraph. Prepare this legacy
 // backend policy only once an actual recognition compile needs it, before
 // that compile can create descriptors on its worker threads.
 static std::once_flag prepare;
 std::call_once(prepare,install_nonmemref);
 if(!nonmemref_ready)return 22;
 recognition_active.store(true,std::memory_order_release);
 std::string replacement;
 if(detector_model(path)){if(package[0]!='/')return 22;replacement=std::string(package)+"/detector/model.mil";if(access(replacement.c_str(),R_OK))return 22;}
 uint64_t previous=0;if(e5rt_e5_compiler_options_get_compute_device_types_mask(options,&previous))return 22;MaskRestore restore{options,previous};
 if(e5rt_e5_compiler_options_set_compute_device_types_mask(options,2))return 22;
 int result=e5rt_e5_compiler_compile(compiler,replacement.empty()?model:const_cast<char*>(replacement.c_str()),options,out);
 if(!result&&out&&*static_cast<void**>(out)&&hao_live_text_record_library)
     hao_live_text_record_library(*static_cast<void**>(out),detector_model(path)?2:1);
 int restored=restore.restore();return result?result:(restored?22:0);
}
extern "C" int live_text_compiler_create(void **out){
 const char *cache=getenv("HAO_LIVE_TEXT_E5_CACHE");if(!root()||!cache||!*cache)return e5rt_e5_compiler_create(out);
 void *config=nullptr;int result=e5rt_e5_compiler_config_options_create(&config);
 if(!result)result=e5rt_e5_compiler_config_options_set_cache_bundle_location(config,cache);
 if(!result)result=e5rt_e5_compiler_create_with_config(out,config);
 if(config)e5rt_e5_compiler_config_options_release(&config);return result;
}
#define HOOK(replacement,original) __attribute__((used,section("__DATA,__interpose"))) static const struct {const void *r,*o;} hook_##original={(void*)replacement,(void*)original};
HOOK(live_text_compile,e5rt_e5_compiler_compile)
HOOK(live_text_compiler_create,e5rt_e5_compiler_create)
