#import <Foundation/Foundation.h>
#import <objc/message.h>
#import <objc/runtime.h>
#include <CommonCrypto/CommonDigest.h>
#include <dlfcn.h>
#include <mutex>
#include "frontend.h"
#include "lower.h"

typedef void *Ref;
// Use the compiler shipped with the legacy graphics stack; no host toolchain at runtime.
#define FB_LLVM_API(X) \
 X(Ref,ContextCreate,(void)) \
 X(void,ContextDispose,(Ref)) \
 X(Ref,CreateMemoryBufferWithMemoryRangeCopy,(const char *,size_t,const char *)) \
 X(int,ParseBitcodeInContext,(Ref,Ref,Ref *,char **)) \
 X(int,ParseIRInContext,(Ref,Ref,Ref *,char **)) \
 X(char *,PrintModuleToString,(Ref)) \
 X(void,DisposeMessage,(char *)) \
 X(void,DisposeModule,(Ref)) \
 X(void,DisposeMemoryBuffer,(Ref)) \
 X(int,VerifyModule,(Ref,int,char **)) \
 X(Ref,WriteBitcodeToMemoryBuffer,(Ref)) \
 X(const char *,GetBufferStart,(Ref)) \
 X(size_t,GetBufferSize,(Ref))
#define DECL(ret,name,args) static ret (*L##name)args;
FB_LLVM_API(DECL)
#undef DECL
static std::once_flag llvmOnce;
static std::string llvmError;
static char variantsKey;
static void loadLLVM() {
    // FB_LLVM is a harness override. Installed use resolves the stack's own library.
    const char *path=getenv("METAL_FRAMEBUFFER_LLVM");
    if(!path)path="/System/Library/PrivateFrameworks/GPUCompiler.framework/Versions/32023/Libraries/libLLVM.dylib";
    void *h=dlopen(path,RTLD_NOW|RTLD_LOCAL);
    if(!h) {llvmError=dlerror();return;}
#define LOAD(ret,name,args) L##name=(ret(*)args)dlsym(h,"LLVM" #name); if(!L##name)llvmError="Missing LLVM" #name;
    FB_LLVM_API(LOAD)
#undef LOAD
}
struct Module {
    Ref context=nullptr,module=nullptr;
    ~Module(){if(module)LDisposeModule(module);if(context)LContextDispose(context);}
};
static void check(int failed,char *message,const char *fallback) {
    std::string why=message?message:fallback;if(message)LDisposeMessage(message);
    if(failed)throw std::runtime_error(why);
}
static void u16(NSMutableData *data,uint16_t n){[data appendBytes:&n length:2];}
static void u32(NSMutableData *data,uint32_t n){[data appendBytes:&n length:4];}
static void u64(NSMutableData *data,uint64_t n){[data appendBytes:&n length:8];}
static void tag(NSMutableData *data,const char *name,NSData *value) {
    [data appendBytes:name length:4];u16(data,(uint16_t)value.length);[data appendData:value];
}
static NSData *library(NSData *air,NSString *name,const std::string &ir,bool vertex) {
    // A specialized fragment has no library-level constants or vertex attributes.
    // Its input/output reflection is in AIR. Let Metal form every cache key anew.
    NSMutableData *entry=[NSMutableData new],*field=[NSMutableData new];
    NSData *utf8=[name dataUsingEncoding:NSUTF8StringEncoding];[field appendData:utf8];uint8_t zero=0;[field appendBytes:&zero length:1];tag(entry,"NAME",field);
    uint8_t type=vertex?0:1;tag(entry,"TYPE",[NSData dataWithBytes:&type length:1]);
    unsigned char hash[CC_SHA256_DIGEST_LENGTH];CC_SHA256(air.bytes,(CC_LONG)air.length,hash);tag(entry,"HASH",[NSData dataWithBytes:hash length:sizeof(hash)]);
    field=[NSMutableData new];u64(field,air.length);tag(entry,"MDSZ",field);
    field=[NSMutableData new];u64(field,0);u64(field,0);u64(field,0);tag(entry,"OFFT",field);
    // Derive VERS from this module, including its post-specialization AIR version.
    field=[NSMutableData new];
    for(const auto &key: {std::string("air.version"),std::string("air.language_version")}) {
        auto version=fbAirVersion(ir,key);
        u16(field,(uint16_t)version.first);u16(field,(uint16_t)version.second);
    }
    tag(entry,"VERS",field);[entry appendBytes:"ENDT" length:4];
    NSMutableData *functions=[NSMutableData new];u32(functions,1);u32(functions,(uint32_t)entry.length+4);[functions appendData:entry];[functions appendBytes:"ENDT" length:4];
    uint64_t publicOffset=88+functions.length,privateOffset=publicOffset+8,bitcodeOffset=privateOffset+8;
    const unsigned char header[]={ 'M','T','L','B',1,0x80,2,0,7,0,0,0x81,13,0,0,0 };
    NSMutableData *data=[NSMutableData dataWithBytes:header length:sizeof(header)];
    for(uint64_t n: {bitcodeOffset+air.length,uint64_t(88),uint64_t(entry.length+4),publicOffset,uint64_t(8),privateOffset,uint64_t(8),bitcodeOffset,uint64_t(air.length)})u64(data,n);
    [data appendData:functions];u32(data,4);[data appendBytes:"ENDT" length:4];u32(data,4);[data appendBytes:"ENDT" length:4];[data appendData:air];return data;
}
static id<MTLFunction> transform(id<MTLFunction> function,NSUInteger samples,bool vertex,NSError **error) {
    if(!function)return function;
    SEL selector=sel_registerName("bitcodeData");
    if(![function respondsToSelector:selector])return function;
    @synchronized(function) {
        NSMutableDictionary *variants=objc_getAssociatedObject(function,&variantsKey);
        NSNumber *key=@(samples>1);if(variants[key])return variants[key]==NSNull.null?function:variants[key];
        try {
            std::call_once(llvmOnce,loadLLVM);if(!llvmError.empty())throw std::runtime_error(llvmError);
            NSData *data=((id(*)(id,SEL))objc_msgSend)(function,selector);
            if(!data.length)throw std::runtime_error("Fragment AIR is unavailable");
            Module original;original.context=LContextCreate();
            Ref buffer=LCreateMemoryBufferWithMemoryRangeCopy((const char *)data.bytes,data.length,"framebuffer");char *message=nullptr;
            int status=LParseBitcodeInContext(original.context,buffer,&original.module,&message);LDisposeMemoryBuffer(buffer);check(status,message,"Cannot parse fragment AIR");
            char *printed=LPrintModuleToString(original.module);std::string ir=printed;LDisposeMessage(printed);
            std::string draw=fbDrawInputs(ir,vertex);
            FBLowered lowered=vertex?FBLowered{draw,{}}:fbLower(draw,samples>1);
            if(!vertex && lowered.plans.empty())lowered.text=ir;
            id<MTLFunction> result=function;
            if(lowered.text!=ir) {
                Module rewritten;rewritten.context=LContextCreate();
                buffer=LCreateMemoryBufferWithMemoryRangeCopy(lowered.text.data(),lowered.text.size(),"framebuffer");message=nullptr;
                status=LParseIRInContext(rewritten.context,buffer,&rewritten.module,&message);check(status,message,"Cannot assemble lowered AIR");
                message=nullptr;status=LVerifyModule(rewritten.module,2,&message);check(status,message,"Invalid lowered AIR");
                buffer=LWriteBitcodeToMemoryBuffer(rewritten.module);
                NSData *air=[NSData dataWithBytes:LGetBufferStart(buffer) length:LGetBufferSize(buffer)];LDisposeMemoryBuffer(buffer);
                NSData *container=library(air,function.name,lowered.text,vertex);
                dispatch_data_t bytes=dispatch_data_create(container.bytes,container.length,NULL,DISPATCH_DATA_DESTRUCTOR_DEFAULT);
                id<MTLLibrary> lib=[function.device newLibraryWithData:bytes error:error];if(!lib)return nil;
                result=[lib newFunctionWithName:function.name];
                if(!result)throw std::runtime_error("Lowered fragment missing from new library");
            }
            if(!variants) {variants=[NSMutableDictionary new];objc_setAssociatedObject(function,&variantsKey,variants,OBJC_ASSOCIATION_RETAIN_NONATOMIC);}
            // Do not retain the original function through its own associated dictionary.
            variants[key]=result==function?(id)NSNull.null:result;
            return result;
        } catch(const std::exception &e) {
            if(error)*error=[NSError errorWithDomain:@"FramebufferLowering" code:1 userInfo:@{NSLocalizedDescriptionKey:@(e.what())}];return nil;
        }
    }
}
id<MTLFunction> FBFramebufferFunction(id<MTLFunction> function,NSUInteger samples,NSError **error) {
    return function.functionType==MTLFunctionTypeFragment?transform(function,samples,false,error):function;
}
id<MTLFunction> FBFramebufferVertexFunction(id<MTLFunction> function,NSError **error) {
    return function.functionType==MTLFunctionTypeVertex?transform(function,1,true,error):function;
}
