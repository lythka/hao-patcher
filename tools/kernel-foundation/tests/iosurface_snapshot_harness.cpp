#include <cassert>
#include <cstdint>
#include <cstring>
#include <cstdio>
#include <vector>
#include "../src/iosurface_snapshot.h"

struct Descriptor { uint64_t size; std::vector<unsigned char> bytes; };
struct Arguments {
    const void *structureInput;
    unsigned structureInputSize;
    Descriptor *structureInputDescriptor;
    unsigned outputSize;
    unsigned *output;
};
enum { bad=1, no_memory=2, copy_error=3, prepare_error=4, complete_error=5, parser_error=6 };
struct Backend {
    Descriptor descriptor;
    std::vector<unsigned char> buffer;
    Arguments *caller;
    int allocs=0, releases=0, prepares=0, completes=0, reads=0, calls=0;
    bool alloc_fail=false, prepare_fail=false, complete_fail=false, reject=false;
    int short_read=0;
    uint64_t next_offset=0;
    Backend(uint64_t size): descriptor{size,{}} {
        if(size<1000000) descriptor.bytes.resize(size,0x5a);
    }
    uint64_t length(Descriptor *md) { assert(md==&descriptor); return md->size; }
    void *allocate(uint64_t size) {
        allocs++;
        if(alloc_fail || size>=1000000) return nullptr;
        buffer.resize(size); return buffer.data();
    }
    void release(void *ptr,uint64_t size) {
        assert(ptr==buffer.data() && size==descriptor.size);
        assert(releases++==0);
        std::memset(ptr,0xcc,size);
    }
    int prepare(Descriptor *md) {
        assert(md==&descriptor && allocs==1); prepares++; return prepare_fail?prepare_error:0;
    }
    int complete(Descriptor *md) {
        assert(md==&descriptor && prepares==1 && !prepare_fail && completes==0);
        completes++; return complete_fail?complete_error:0;
    }
    uint64_t read(Descriptor *md,uint64_t offset,void *to,uint64_t size) {
        assert(md==&descriptor && prepares==1 && !completes && !prepare_fail);
        assert(offset==next_offset && size<=65536 && offset+size<=descriptor.size);
        reads++; next_offset+=size;
        if(reads==short_read) return size-1;
        std::memcpy(to,md->bytes.data()+offset,size); return size;
    }
    int original(Arguments *args) {
        calls++;
        if(caller->structureInputDescriptor) {
            assert(args!=caller && args->structureInputDescriptor==nullptr);
            assert(args->structureInput==buffer.data() && args->structureInputSize==descriptor.size);
            assert(completes==1 && releases==0);
            // Mutating the provider after capture cannot change what the parser sees.
            std::memset(descriptor.bytes.data(),0xa5,descriptor.bytes.size());
            for(auto byte:buffer) assert(byte==0x5a);
        } else assert(args==caller);
        args->outputSize=7;
        *args->output=0x12345678;
        return reject?parser_error:0;
    }
    int bad_argument(){ return bad; }
    int no_memory(){ return ::no_memory; }
    int copy_error(){ return ::copy_error; }
    int run(bool inline_input=false) {
        unsigned output=0;
        const unsigned char original_input[]={1,2,3};
        Arguments args{original_input,3,inline_input?nullptr:&descriptor,99,&output};
        caller=&args;
        int result=iosurface_snapshot(*this,&args);
        assert(args.structureInput==original_input && args.structureInputSize==3);
        assert(args.structureInputDescriptor==(inline_input?nullptr:&descriptor));
        assert(args.output==&output);
        assert(args.outputSize==(calls?7U:99U));
        assert(output==(calls?0x12345678U:0));
        return result;
    }
};

int main() {
    for(auto size:{13ULL,8192ULL,65536ULL,65537ULL,200000ULL}) {
        Backend b(size); assert(b.run()==0);
        assert(b.calls==1 && b.allocs==1 && b.releases==1 && b.prepares==1 && b.completes==1);
        assert(b.reads==int((size+65535)/65536));
    }
    { Backend b(8192); assert(b.run(true)==0); assert(b.allocs==0 && b.prepares==0 && b.calls==1); }
    for(auto size:{0ULL,12ULL,0x100000000ULL,0xffffffffffffffffULL}) {
        Backend b(size); assert(b.run()==bad); assert(!b.allocs && !b.prepares && !b.calls);
    }
    { Backend b(0xffffffff); assert(b.run()==no_memory); assert(b.allocs==1 && !b.prepares && !b.releases); }
    { Backend b(8192); b.alloc_fail=true; assert(b.run()==no_memory); assert(!b.prepares && !b.releases); }
    { Backend b(8192); b.prepare_fail=true; assert(b.run()==prepare_error); assert(!b.completes && !b.reads && !b.calls && b.releases==1); }
    for(int failed_read:{1,2,3}) {
        Backend b(200000); b.short_read=failed_read; assert(b.run()==copy_error);
        assert(b.reads==failed_read && b.completes==1 && b.releases==1 && !b.calls);
    }
    { Backend b(8192); b.complete_fail=true; assert(b.run()==complete_error); assert(!b.calls && b.releases==1); }
    { Backend b(8192); b.reject=true; assert(b.run()==parser_error); assert(b.calls==1 && b.releases==1); }
    std::puts("IOSurface snapshot: inputs, cleanup, output propagation and private parser bytes OK");
}
