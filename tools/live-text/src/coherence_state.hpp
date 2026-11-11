#pragma once
#include <algorithm>
#include <cstdint>
#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <string>
#include <vector>

// Native handles are observers, never assumed to own their backing object's
// lifetime. Encoded streams, retained ports and in-flight submissions may outlive
// the caller's operation/buffer handle. These shared states mirror those uses.
template<class Surface> class CoherenceState {
public:
 using Ref=std::shared_ptr<Surface>;
 struct Buffer {Ref surface;}; using BufferRef=std::shared_ptr<Buffer>;
 struct Origin {int kind;}; using OriginRef=std::shared_ptr<Origin>;
 struct OriginHandle {OriginRef origin;size_t references=1;};using OriginHandleRef=std::shared_ptr<OriginHandle>;
 using OriginMap=std::map<void*,OriginHandleRef>;
 struct Operation {std::map<std::string,Ref> inputs;OriginRef origin;}; using OperationRef=std::shared_ptr<Operation>;
 struct Port {OperationRef operation;std::string name;}; using PortRef=std::shared_ptr<Port>;
 struct Stream {std::vector<OperationRef> operations;}; using StreamRef=std::shared_ptr<Stream>;
 using Capture=std::vector<Ref>;
 struct BindCheck {bool detector=false;Ref surface;};
 struct Layout {uint32_t format;size_t width,height,row_bytes,allocation;};
 static bool valid_detector_layout(const Layout &s){return s.format==0x42475241&&s.width==480&&s.height==480&&s.row_bytes==1920&&s.allocation>=size_t(1920)*480;}
private:
 std::mutex mutex;
 OriginMap libraries,functions,options;
 std::map<void*,BufferRef> buffers;
 std::map<void*,OperationRef> operations;
 std::map<void*,PortRef> ports;
 std::map<void*,StreamRef> streams;
 OperationRef operation(void *h){auto &p=operations[h];if(!p)p=std::make_shared<Operation>();return p;}
 template<class Map> static typename Map::mapped_type take(Map &m,void *h){auto i=m.find(h);if(i==m.end())return {};auto p=std::move(i->second);m.erase(i);return p;}
 template<class Map> static void restore(Map &m,void *h,typename Map::mapped_type p){if(p)m.emplace(h,std::move(p));}
static void observed_origin(OriginMap &m,void *h,OriginRef origin){
  auto i=m.find(h);int kind=origin?origin->kind:0;
  if(i!=m.end()&&(i->second->origin?i->second->origin->kind:0)==kind){++i->second->references;return;}
  m[h]=std::make_shared<OriginHandle>(OriginHandle{std::move(origin),1});
 }
 static OriginHandleRef take_origin(OriginMap &m,void *h){auto i=m.find(h);if(i==m.end())return {};auto p=i->second;if(!--p->references)m.erase(i);return p;}
 static void restore_origin(OriginMap &m,void *h,OriginHandleRef p){if(!p)return;auto i=m.find(h);if(i==m.end()){++p->references;m.emplace(h,std::move(p));}else if(i->second==p)++p->references;}
public:
 void recorded_library(void *h,int kind){std::lock_guard<std::mutex> lock(mutex);observed_origin(libraries,h,std::make_shared<Origin>(Origin{kind}));}
 void retained_function(void *library,void *function){std::lock_guard<std::mutex> lock(mutex);auto p=libraries.find(library);observed_origin(functions,function,p==libraries.end()?OriginRef{}:p->second->origin);}
 void created_options(void *function,void *opts){std::lock_guard<std::mutex> lock(mutex);auto p=functions.find(function);observed_origin(options,opts,p==functions.end()?OriginRef{}:p->second->origin);}
 void created_operation(void *opts,void *op){std::lock_guard<std::mutex> lock(mutex);auto fresh=std::make_shared<Operation>();auto p=options.find(opts);if(p!=options.end())fresh->origin=p->second->origin;operations[op]=std::move(fresh);}
 int operation_kind(void *op){std::lock_guard<std::mutex> lock(mutex);auto p=operations.find(op);return p==operations.end()||!p->second->origin?0:p->second->origin->kind;}
 BindCheck check_bind(void *port,void *buffer){std::lock_guard<std::mutex> lock(mutex);auto p=ports.find(port);if(p==ports.end()||p->second->name!="img_input"||!p->second->operation->origin||p->second->operation->origin->kind!=2)return {};auto b=buffers.find(buffer);return {true,b==buffers.end()?Ref{}:b->second->surface};}
 void created_buffer(void *h,Ref surface){std::lock_guard<std::mutex> lock(mutex);buffers[h]=std::make_shared<Buffer>(Buffer{std::move(surface)});}
 void retained_input(void *op,const char *name,void *port){std::lock_guard<std::mutex> lock(mutex);ports[port]=std::make_shared<Port>(Port{operation(op),name});}
 void retained_output(void *port){std::lock_guard<std::mutex> lock(mutex);ports.erase(port);}
 Ref bound(void *port,void *buffer){
  std::lock_guard<std::mutex> lock(mutex);auto p=ports.find(port);if(p==ports.end())return {};
  auto b=buffers.find(buffer);auto &inputs=p->second->operation->inputs;
  if(b==buffers.end()){inputs.erase(p->second->name);return {};}
  Ref s=b->second->surface;inputs[p->second->name]=s;return s;
 }
 void created_stream(void *h){std::lock_guard<std::mutex> lock(mutex);streams[h]=std::make_shared<Stream>();}
 void encoded(void *h,void *op){std::lock_guard<std::mutex> lock(mutex);auto &stream=streams[h];if(!stream)stream=std::make_shared<Stream>();auto p=operation(op);auto &v=stream->operations;if(std::find(v.begin(),v.end(),p)==v.end())v.push_back(p);}
 Capture capture(void *h){
  std::lock_guard<std::mutex> lock(mutex);Capture result;auto i=streams.find(h);if(i==streams.end())return result;std::set<Surface*> seen;
  for(auto &op:i->second->operations)for(auto &input:op->inputs)if(input.second&&seen.insert(input.second.get()).second)result.push_back(input.second);
  return result;
 }
 // Detach BEFORE native release, preventing a newly reused address from finding
 // stale state. On failed release restore only if no newer generation exists.
 OriginHandleRef take_library(void *h){std::lock_guard<std::mutex> lock(mutex);return take_origin(libraries,h);}
 OriginHandleRef take_function(void *h){std::lock_guard<std::mutex> lock(mutex);return take_origin(functions,h);}
 OriginHandleRef take_options(void *h){std::lock_guard<std::mutex> lock(mutex);return take_origin(options,h);}
 void restore_library(void *h,OriginHandleRef p){std::lock_guard<std::mutex> lock(mutex);restore_origin(libraries,h,std::move(p));}
 void restore_function(void *h,OriginHandleRef p){std::lock_guard<std::mutex> lock(mutex);restore_origin(functions,h,std::move(p));}
 void restore_options(void *h,OriginHandleRef p){std::lock_guard<std::mutex> lock(mutex);restore_origin(options,h,std::move(p));}
 BufferRef take_buffer(void *h){std::lock_guard<std::mutex> lock(mutex);return take(buffers,h);}
 PortRef take_port(void *h){std::lock_guard<std::mutex> lock(mutex);return take(ports,h);}
 OperationRef take_operation(void *h){std::lock_guard<std::mutex> lock(mutex);return take(operations,h);}
 StreamRef take_stream(void *h){std::lock_guard<std::mutex> lock(mutex);return take(streams,h);}
 void restore_buffer(void *h,BufferRef p){std::lock_guard<std::mutex> lock(mutex);restore(buffers,h,std::move(p));}
 void restore_port(void *h,PortRef p){std::lock_guard<std::mutex> lock(mutex);restore(ports,h,std::move(p));}
 void restore_operation(void *h,OperationRef p){std::lock_guard<std::mutex> lock(mutex);restore(operations,h,std::move(p));}
 void restore_stream(void *h,StreamRef p){std::lock_guard<std::mutex> lock(mutex);restore(streams,h,std::move(p));}
#ifdef COHERENCE_TEST
 std::vector<size_t> origin_counts(){std::lock_guard<std::mutex> lock(mutex);return {libraries.size(),functions.size(),options.size()};}
 std::vector<size_t> counts(){std::lock_guard<std::mutex> lock(mutex);return {buffers.size(),operations.size(),ports.size(),streams.size()};}
#endif
};
