// Quiet opt-in IOSurface input coherence for the native26A428 E5 wrapper ABI.
// Derived from verified trace_tensor_values.mm bindings/completion callback.
// 26B131: every e5rt_* function used here is in x86_64 Espresso (dyld_shared_cache_x86_64, 27.2.1).
// No snapshots, descriptor reads, tracing, CPU copies or additional executions.
#import <IOSurface/IOSurface.h>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include "coherence_state.hpp"
#include "diagnostic_counts.hpp"
#include <cstdio>
extern "C" {
int e5rt_program_library_retain_program_function(void*,const char*,void**) __attribute__((weak_import));
int e5rt_precompiled_compute_op_create_options_create_with_program_function(void**,void*) __attribute__((weak_import));
int e5rt_execution_stream_operation_create_precompiled_compute_operation_with_options(void**,void*) __attribute__((weak_import));
int e5rt_program_library_release(void**) __attribute__((weak_import));
int e5rt_program_function_release(void**) __attribute__((weak_import));
int e5rt_precompiled_compute_op_create_options_release(void**) __attribute__((weak_import));
int e5rt_buffer_object_create_from_iosurface(void**,IOSurfaceRef) __attribute__((weak_import));
int e5rt_buffer_object_release(void**) __attribute__((weak_import));
int e5rt_io_port_bind_buffer_object(void*,void*) __attribute__((weak_import));
int e5rt_io_port_release(void**) __attribute__((weak_import));
int e5rt_execution_stream_operation_retain_input_port(void*,const char*,void**) __attribute__((weak_import));
int e5rt_execution_stream_operation_retain_output_port(void*,const char*,void**) __attribute__((weak_import));
int e5rt_execution_stream_operation_release(void**) __attribute__((weak_import));
typedef void (^Completion)(uint64_t,uint64_t,int);
int e5rt_execution_stream_create(void**) __attribute__((weak_import));
int e5rt_execution_stream_release(void**) __attribute__((weak_import));
int e5rt_execution_stream_execute_sync(void*) __attribute__((weak_import));
int e5rt_execution_stream_submit_async(void*,Completion) __attribute__((weak_import));
int e5rt_execution_stream_submit_async_with_timeout(void*,Completion,uint64_t) __attribute__((weak_import));
int e5rt_execution_stream_encode_operation(void*,void*) __attribute__((weak_import));
}
extern "C" bool hao_live_text_recognition_active(void) __attribute__((weak_import));
using State=CoherenceState<__IOSurface>;
static State &state(){static State value;return value;}
static bool opted_in(){const char *root=getenv("HAO_LIVE_TEXT_ROOT");return root&&*root&&strcmp(root,"0");}
static bool enabled(){return hao_live_text_recognition_active&&hao_live_text_recognition_active();}
static void diagnostic(CoherenceDiagnosticCounts::Event event){
 static const bool requested=[](){const char *v=getenv("HAO_LIVE_TEXT_DIAGNOSTICS");return v&&!strcmp(v,"1");}();
 if(!requested)return;
 static CoherenceDiagnosticCounts counts;auto sample=counts.record(event);if(!sample.emit)return;
 static const char *names[]={"recognizer-library","detector-library","recognizer-operation","detector-operation","detector-bind-pass","detector-bind-refusal"};
 fprintf(stderr,"HAO_LIVE_TEXT_DIAGNOSTIC event=%s count=%llu detector_bind_pass=%llu detector_bind_refusal=%llu\n",names[event],(unsigned long long)sample.count,(unsigned long long)sample.passes,(unsigned long long)sample.refusals);
}
static State::Ref owned(IOSurfaceRef s){CFRetain(s);return State::Ref(s,[](auto *p){CFRelease(p);});}
static void coherent(const State::Ref &s){if(s&&!IOSurfaceLock(s.get(),kIOSurfaceLockReadOnly,nullptr))IOSurfaceUnlock(s.get(),kIOSurfaceLockReadOnly,nullptr);}
static void coherent(const State::Capture &s){for(auto &p:s)coherent(p);}
extern "C" void hao_live_text_record_library(void *library,int kind){if(opted_in()&&library&&(kind==1||kind==2)){state().recorded_library(library,kind);diagnostic(kind==2?CoherenceDiagnosticCounts::DetectorLibrary:CoherenceDiagnosticCounts::RecognizerLibrary);}}
extern "C" int coherence_function(void *library,const char *name,void **out){int r=e5rt_program_library_retain_program_function(library,name,out);if(opted_in()&&!r&&out&&*out)state().retained_function(library,*out);return r;}
extern "C" int coherence_options(void **out,void *function){int r=e5rt_precompiled_compute_op_create_options_create_with_program_function(out,function);if(opted_in()&&!r&&out&&*out)state().created_options(function,*out);return r;}
extern "C" int coherence_operation(void **out,void *options){int r=e5rt_execution_stream_operation_create_precompiled_compute_operation_with_options(out,options);if(opted_in()&&!r&&out&&*out){state().created_operation(options,*out);int kind=state().operation_kind(*out);if(kind)diagnostic(kind==2?CoherenceDiagnosticCounts::DetectorOperation:CoherenceDiagnosticCounts::RecognizerOperation);}return r;}
extern "C" int coherence_create(void **out,IOSurfaceRef surface){int r=e5rt_buffer_object_create_from_iosurface(out,surface);if(opted_in()&&!r&&out&&*out&&surface)state().created_buffer(*out,owned(surface));return r;}
extern "C" int coherence_input(void *op,const char *name,void **out){int r=e5rt_execution_stream_operation_retain_input_port(op,name,out);if(enabled()&&!r&&op&&name&&out&&*out)state().retained_input(op,name,*out);return r;}
extern "C" int coherence_output(void *op,const char *name,void **out){int r=e5rt_execution_stream_operation_retain_output_port(op,name,out);if(enabled()&&!r&&out&&*out)state().retained_output(*out);return r;}
extern "C" int coherence_bind(void *port,void *buffer){
 bool detector=false;
 if(opted_in()){
  auto check=state().check_bind(port,buffer);
  if(check.detector){
   detector=true;
   auto s=check.surface.get();
   if(!s||!State::valid_detector_layout({IOSurfaceGetPixelFormat(s),IOSurfaceGetWidth(s),IOSurfaceGetHeight(s),IOSurfaceGetBytesPerRow(s),IOSurfaceGetAllocSize(s)})){diagnostic(CoherenceDiagnosticCounts::DetectorBindRefusal);return 22;}
  }
 }
 int r=e5rt_io_port_bind_buffer_object(port,buffer);if(detector)diagnostic(r?CoherenceDiagnosticCounts::DetectorBindRefusal:CoherenceDiagnosticCounts::DetectorBindPass);if(enabled()&&!r)coherent(state().bound(port,buffer));return r;}
extern "C" int coherence_stream_create(void **out){int r=e5rt_execution_stream_create(out);if(enabled()&&!r&&out&&*out)state().created_stream(*out);return r;}
extern "C" int coherence_encode(void *stream,void *op){int r=e5rt_execution_stream_encode_operation(stream,op);if(enabled()&&!r&&stream&&op)state().encoded(stream,op);return r;}
#define RELEASE_WRAPPER(name,kind,original) \
extern "C" int name(void **handle){ \
 if(!opted_in())return original(handle);void *old=handle?*handle:nullptr; \
 auto saved=state().take_##kind(old);int r=original(handle); \
 if(r)state().restore_##kind(old,std::move(saved));return r; \
}
RELEASE_WRAPPER(coherence_library_release,library,e5rt_program_library_release)
RELEASE_WRAPPER(coherence_function_release,function,e5rt_program_function_release)
RELEASE_WRAPPER(coherence_options_release,options,e5rt_precompiled_compute_op_create_options_release)
RELEASE_WRAPPER(coherence_buffer_release,buffer,e5rt_buffer_object_release)
RELEASE_WRAPPER(coherence_port_release,port,e5rt_io_port_release)
RELEASE_WRAPPER(coherence_operation_release,operation,e5rt_execution_stream_operation_release)
RELEASE_WRAPPER(coherence_stream_release,stream,e5rt_execution_stream_release)
extern "C" int coherence_execute(void *stream){if(!enabled())return e5rt_execution_stream_execute_sync(stream);auto saved=state().capture(stream);coherent(saved);return e5rt_execution_stream_execute_sync(stream);}
struct Flight {std::mutex mutex;State::Capture surfaces;void clear(){std::lock_guard<std::mutex> lock(mutex);surfaces.clear();}};
static Completion completion(void *stream,Completion callback){
 auto flight=std::make_shared<Flight>();flight->surfaces=state().capture(stream);coherent(flight->surfaces);
 // The copied block owns this capture through asynchronous GPU completion.
 // Clear after forwarding even if the native runtime retains the finished block.
 return ^(uint64_t a,uint64_t b,int error){@try{callback(a,b,error);}@finally{flight->clear();}};
}
extern "C" int coherence_submit(void *stream,Completion callback){
 // Null-callback submissions retain native semantics; this wrapper cannot infer
 // their completion lifetime, so they are deliberately not wrapped.
 return e5rt_execution_stream_submit_async(stream,enabled()&&callback?completion(stream,callback):callback);
}
extern "C" int coherence_submit_timeout(void *stream,Completion callback,uint64_t timeout){return e5rt_execution_stream_submit_async_with_timeout(stream,enabled()&&callback?completion(stream,callback):callback,timeout);}
#define HOOK(replacement,original) __attribute__((used,section("__DATA,__interpose"))) static const struct {const void *r,*o;} hook_##original={(void*)replacement,(void*)original};
HOOK(coherence_function,e5rt_program_library_retain_program_function)
HOOK(coherence_options,e5rt_precompiled_compute_op_create_options_create_with_program_function)
HOOK(coherence_operation,e5rt_execution_stream_operation_create_precompiled_compute_operation_with_options)
HOOK(coherence_library_release,e5rt_program_library_release)
HOOK(coherence_function_release,e5rt_program_function_release)
HOOK(coherence_options_release,e5rt_precompiled_compute_op_create_options_release)
HOOK(coherence_create,e5rt_buffer_object_create_from_iosurface)
HOOK(coherence_buffer_release,e5rt_buffer_object_release)
HOOK(coherence_input,e5rt_execution_stream_operation_retain_input_port)
HOOK(coherence_output,e5rt_execution_stream_operation_retain_output_port)
HOOK(coherence_port_release,e5rt_io_port_release)
HOOK(coherence_operation_release,e5rt_execution_stream_operation_release)
HOOK(coherence_bind,e5rt_io_port_bind_buffer_object)
HOOK(coherence_encode,e5rt_execution_stream_encode_operation)
HOOK(coherence_stream_create,e5rt_execution_stream_create)
HOOK(coherence_stream_release,e5rt_execution_stream_release)
HOOK(coherence_execute,e5rt_execution_stream_execute_sync)
HOOK(coherence_submit,e5rt_execution_stream_submit_async)
HOOK(coherence_submit_timeout,e5rt_execution_stream_submit_async_with_timeout)
