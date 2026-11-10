// Reuse the repository's modeled legacy device and real compiler, without a GPU.
#import "frontend.h"
#define main replay_main
#include "replay.m"
#undef main

static NSString *hashOf(id function) {
    if(!function)return @"-";
    const unsigned char *hash=MSG(const unsigned char *(*)(id,SEL),function,sel_registerName("bitCodeHash"));
    NSMutableString *s=[NSMutableString new];for(unsigned i=0;i<32;++i)[s appendFormat:@"%02x",hash[i]];return [s autorelease];
}
int main(int argc,char **argv) { @autoreleasepool {
    if(argc<4)return 2;
    if(!dlopen(argv[1],RTLD_NOW|RTLD_GLOBAL))return 2;
    if(![[NSBundle bundleWithPath:@(argv[2])] load])return 2;
    Class c=NSClassFromString(@(getenv("RP_DEVICE_CLASS")));
    SEL selector=@selector(newRenderPipelineStateWithDescriptor:options:reflection:error:);
    Method method=class_getInstanceMethod(c,selector);IMP original=method_getImplementation(method);
    IMP replacement=imp_implementationWithBlock(^id(id device,MTLRenderPipelineDescriptor *input,NSUInteger flags,id *out,NSError **error) {
        MTLRenderPipelineDescriptor *desc=[[input copy] autorelease];
        NSString *before=hashOf(desc.fragmentFunction);
        if(getenv("FB_LIVE"))desc.fragmentFunction=FBFramebufferFunction(desc.fragmentFunction,desc.rasterSampleCount,error);
        if(input.fragmentFunction && !desc.fragmentFunction)return nil;
        id reflection=nil;
        id result=((id(*)(id,SEL,id,NSUInteger,id *,NSError **))original)(device,selector,desc,flags|MTLPipelineOptionArgumentInfo,&reflection,error);
        NSMutableArray *bindings=[NSMutableArray new];
        for(MTLArgument *arg in [reflection fragmentArguments])
            [bindings addObject:@{@"name":arg.name,@"index":@(arg.index),@"type":@(arg.type)}];
        NSDictionary *row=@{@"function":desc.fragmentFunction.name?:@"-",@"before":before,@"after":hashOf(desc.fragmentFunction),@"bindings":bindings,@"pipeline":@(result!=nil)};
        NSData *json=[NSJSONSerialization dataWithJSONObject:row options:0 error:NULL];
        fprintf(stderr,"FB_IDENTITY %.*s\n",(int)json.length,(const char *)json.bytes);[bindings release];
        if(out)*out=reflection;return result;
    });
    class_replaceMethod(c,selector,replacement,method_getTypeEncoding(method));
    return replay_main(argc,argv);
}}
