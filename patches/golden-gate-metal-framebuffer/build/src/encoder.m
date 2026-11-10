// Ordered encoder half of the AIR lowering ABI, shared by the driver and probes.
// Pipelines are selected by synthesized reflection, never client/function names.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <objc/runtime.h>
#import "frontend.h"

typedef id (*FBNewPipeline)(id,SEL,id,NSUInteger,id *,NSError **) __attribute__((ns_returns_retained));
typedef id (*FBNewQueue)(id,SEL) __attribute__((ns_returns_retained));
typedef id (*FBNewQueueCount)(id,SEL,NSUInteger) __attribute__((ns_returns_retained));

static char pipelineKey, encoderKey, hooksKey, parallelKey, parallelHooksKey, deviceHooksKey, queueHooksKey, commandHooksKey;
@interface FBState : NSObject
@property MTLRenderPassDescriptor *pass;
@property NSArray<NSDictionary *> *bindings;
@property NSMutableDictionary<NSNumber *, id<MTLTexture>> *clientTextures;
@property NSMutableSet<NSNumber *> *overridden;
@property BOOL busy;
@property NSMutableArray *resources;
@property NSMutableDictionary *views;
@property MTLWinding winding;
@property NSInteger vertexDrawSlot;
@property NSInteger fragmentDrawSlot;
@property NSMutableDictionary *vertexBuffers;
@property NSMutableDictionary *fragmentBuffers;
@end
@implementation FBState
- (instancetype)init { if ((self = [super init])) {
    _clientTextures = [NSMutableDictionary new]; _overridden = [NSMutableSet new];
    _resources = [NSMutableArray new]; _views = [NSMutableDictionary new];
    _vertexDrawSlot=-1;_fragmentDrawSlot=-1;_vertexBuffers=[NSMutableDictionary new];_fragmentBuffers=[NSMutableDictionary new];
} return self; }
@end

static void fail(NSString *why) {
    @throw [NSException exceptionWithName:@"FramebufferEmulationUnsupported" reason:why userInfo:nil];
}
static void hook(Class c, SEL selector, id block) {
    Method method = class_getInstanceMethod(c, selector);
    if (!method) fail(NSStringFromSelector(selector));
    class_replaceMethod(c, selector, imp_implementationWithBlock(block), method_getTypeEncoding(method));
}
static void record(id pipeline, MTLRenderPipelineReflection *reflection) {
    if (!pipeline) return;
    NSMutableArray *bindings = [NSMutableArray new];
    for (MTLArgument *arg in reflection.fragmentArguments) {
        if (!arg.active || arg.type != MTLArgumentTypeTexture || ![arg.name hasPrefix:@"__fbcompat_fb_attachment_"]) continue;
        NSString *number = [arg.name substringFromIndex:@"__fbcompat_fb_attachment_".length];
        NSScanner *scan = [NSScanner scannerWithString:number]; NSInteger attachment = -1;
        if (![scan scanInteger:&attachment] || !scan.atEnd || attachment < 0 || attachment > 7)
            fail(@"Invalid lowered attachment metadata");
        [bindings addObject:@{@"slot":@(arg.index), @"attachment":@(attachment), @"type":@(arg.textureType)}];
    }
    NSInteger vertex=-1,fragment=-1;
    for(MTLArgument *arg in reflection.vertexArguments)if(arg.active && arg.type==MTLArgumentTypeBuffer && [arg.name isEqual:@"__fbcompat_fb_draw"])vertex=arg.index;
    for(MTLArgument *arg in reflection.fragmentArguments)if(arg.active && arg.type==MTLArgumentTypeBuffer && [arg.name isEqual:@"__fbcompat_fb_draw"])fragment=arg.index;
    objc_setAssociatedObject(pipeline, &pipelineKey, @{@"bindings":bindings,@"vertex":@(vertex),@"fragment":@(fragment)}, OBJC_ASSOCIATION_RETAIN_NONATOMIC);
}
static NSUInteger width(MTLPrimitiveType type) {
    switch (type) {
        case MTLPrimitiveTypePoint: return 1;
        case MTLPrimitiveTypeLine: return 2;
        case MTLPrimitiveTypeTriangle: return 3;
        case MTLPrimitiveTypeLineStrip: return 2;
        case MTLPrimitiveTypeTriangleStrip: return 3;
        default: fail(@"Strip topology requires preserving winding and primitive identity"); return 0;
    }
}
static BOOL strip(MTLPrimitiveType type) {return type==MTLPrimitiveTypeLineStrip || type==MTLPrimitiveTypeTriangleStrip;}
static NSUInteger primitiveCount(MTLPrimitiveType type,NSUInteger count) {
    NSUInteger size=width(type);return strip(type)?(count<size?0:count-size+1):count/size;
}
static void prepare(id<MTLRenderCommandEncoder> encoder, FBState *state,NSInteger vertex,NSUInteger instance,NSUInteger primitive) {
    if (!state.pass) fail(@"Framebuffer pipeline has no tracked render pass");
    if (state.pass.renderTargetArrayLength > 1) fail(@"Layered framebuffer input requires layer-aware lowering");
    for (NSDictionary *binding in state.bindings) {
        NSUInteger slot = [binding[@"slot"] unsignedIntegerValue];
        MTLRenderPassColorAttachmentDescriptor *attachment = state.pass.colorAttachments[[binding[@"attachment"] unsignedIntegerValue]];
        id<MTLTexture> texture = state.views[@(slot)] ?: attachment.texture;
        if (!texture) fail(@"Framebuffer input attachment is absent");
        if (attachment.depthPlane) fail(@"3D attachment depth-plane views require a separate lowering");
        if (!state.views[@(slot)] && (attachment.level || attachment.slice || texture.textureType == MTLTextureType2DArray || texture.textureType == MTLTextureTypeCube)) {
            texture = [texture newTextureViewWithPixelFormat:texture.pixelFormat
                textureType:(MTLTextureType)[binding[@"type"] unsignedIntegerValue]
                levels:NSMakeRange(attachment.level, 1) slices:NSMakeRange(attachment.slice, 1)];
            if (!texture) fail(@"Cannot make framebuffer subresource view");
        }
        if (texture.textureType != [binding[@"type"] unsignedIntegerValue]) fail(@"Framebuffer texture type differs from shader lowering");
        if (!state.views[@(slot)]) {state.views[@(slot)]=texture;[state.resources addObject:texture];}
        [encoder setFragmentTexture:texture atIndex:slot];
        [state.overridden addObject:@(slot)];
    }
    uint32_t draw[4]={(uint32_t)vertex,(uint32_t)instance,(uint32_t)primitive,0};
    if(state.vertexDrawSlot>=0)[encoder setVertexBytes:draw length:sizeof(draw) atIndex:state.vertexDrawSlot];
    if(state.fragmentDrawSlot>=0)[encoder setFragmentBytes:draw length:sizeof(draw) atIndex:state.fragmentDrawSlot];
    [encoder textureBarrier];
}
static NSMutableDictionary *bufferState(FBState *state,BOOL vertex) {return vertex?state.vertexBuffers:state.fragmentBuffers;}
static void restoreBuffer(id<MTLRenderCommandEncoder> e,FBState *state,BOOL vertex,NSInteger slot) {
    if(slot<0)return;NSDictionary *binding=bufferState(state,vertex)[@(slot)];
    if(binding[@"bytes"]) {
        NSData *data=binding[@"bytes"];
        if(vertex)[e setVertexBytes:data.bytes length:data.length atIndex:slot];else [e setFragmentBytes:data.bytes length:data.length atIndex:slot];
    } else {
        id buffer=binding[@"buffer"];if(buffer==NSNull.null)buffer=nil;NSUInteger offset=[binding[@"offset"] unsignedIntegerValue];
        if(vertex)[e setVertexBuffer:buffer offset:offset atIndex:slot];else [e setFragmentBuffer:buffer offset:offset atIndex:slot];
    }
}
static void bufferHooks(Class c,BOOL vertex) {
    NSString *stage=vertex?@"Vertex":@"Fragment";
    SEL s=NSSelectorFromString([NSString stringWithFormat:@"set%@Buffer:offset:atIndex:",stage]);IMP p=class_getMethodImplementation(c,s);
    hook(c,s,^(id e,id buffer,NSUInteger offset,NSUInteger index) {
        FBState *state=objc_getAssociatedObject(e,&encoderKey);
        if(!state.busy)bufferState(state,vertex)[@(index)]=@{@"buffer":buffer?:NSNull.null,@"offset":@(offset)};
        ((void(*)(id,SEL,id,NSUInteger,NSUInteger))p)(e,s,buffer,offset,index);
    });
    s=NSSelectorFromString([NSString stringWithFormat:@"set%@Bytes:length:atIndex:",stage]);p=class_getMethodImplementation(c,s);
    hook(c,s,^(id e,const void *bytes,NSUInteger length,NSUInteger index) {
        FBState *state=objc_getAssociatedObject(e,&encoderKey);
        if(!state.busy)bufferState(state,vertex)[@(index)]=@{@"bytes":[NSData dataWithBytes:bytes length:length]};
        ((void(*)(id,SEL,const void *,NSUInteger,NSUInteger))p)(e,s,bytes,length,index);
    });
    s=NSSelectorFromString([NSString stringWithFormat:@"set%@Buffers:offsets:withRange:",stage]);p=class_getMethodImplementation(c,s);
    hook(c,s,^(id e,const id<MTLBuffer> *buffers,const NSUInteger *offsets,NSRange range) {
        FBState *state=objc_getAssociatedObject(e,&encoderKey);
        if(!state.busy)for(NSUInteger i=0;i<range.length;++i)bufferState(state,vertex)[@(range.location+i)]=@{@"buffer":(id)buffers[i]?:NSNull.null,@"offset":@(offsets[i])};
        ((void(*)(id,SEL,const id *,const NSUInteger *,NSRange))p)(e,s,buffers,offsets,range);
    });
    s=NSSelectorFromString([NSString stringWithFormat:@"set%@BufferOffset:atIndex:",stage]);p=class_getMethodImplementation(c,s);
    hook(c,s,^(id e,NSUInteger offset,NSUInteger index) {
        FBState *state=objc_getAssociatedObject(e,&encoderKey);
        if(!state.busy) {
            NSDictionary *old=bufferState(state,vertex)[@(index)];
            bufferState(state,vertex)[@(index)]=@{@"buffer":old[@"buffer"]?:NSNull.null,@"offset":@(offset)};
        }
        ((void(*)(id,SEL,NSUInteger,NSUInteger))p)(e,s,offset,index);
    });
}
static void encoderHooks(Class c) { @synchronized(FBState.class) {
    if (objc_getAssociatedObject(c, &hooksKey)) return;
    objc_setAssociatedObject(c, &hooksKey, @YES, OBJC_ASSOCIATION_RETAIN_NONATOMIC);
    bufferHooks(c,YES);bufferHooks(c,NO);
    SEL s = @selector(setFragmentTexture:atIndex:); IMP p = class_getMethodImplementation(c, s);
    hook(c,s,^(id e,id texture,NSUInteger index) {
        FBState *state = objc_getAssociatedObject(e,&encoderKey);
        if (!state.busy) { if (texture) state.clientTextures[@(index)] = texture; else [state.clientTextures removeObjectForKey:@(index)]; }
        ((void(*)(id,SEL,id,NSUInteger))p)(e,s,texture,index);
    });
    s=@selector(setFrontFacingWinding:);p=class_getMethodImplementation(c,s);
    hook(c,s,^(id e,MTLWinding winding) {
        FBState *state=objc_getAssociatedObject(e,&encoderKey);if(!state.busy)state.winding=winding;
        ((void(*)(id,SEL,MTLWinding))p)(e,s,winding);
    });
    s = @selector(setFragmentTextures:withRange:); p = class_getMethodImplementation(c,s);
    hook(c,s,^(id e,const id<MTLTexture> *textures,NSRange range) {
        FBState *state = objc_getAssociatedObject(e,&encoderKey);
        if (!state.busy) for (NSUInteger i=0;i<range.length;++i) {
            if (textures[i]) state.clientTextures[@(range.location+i)] = textures[i];
            else [state.clientTextures removeObjectForKey:@(range.location+i)];
        }
        ((void(*)(id,SEL,const id *,NSRange))p)(e,s,textures,range);
    });
    s = @selector(setRenderPipelineState:); p = class_getMethodImplementation(c,s);
    hook(c,s,^(id<MTLRenderCommandEncoder> e,id ps) {
        FBState *state = objc_getAssociatedObject(e,&encoderKey);
        if(!state){((void(*)(id,SEL,id))p)(e,s,ps);return;}
        state.busy = YES;
        @try {
            for (NSNumber *slot in state.overridden) [e setFragmentTexture:state.clientTextures[slot] atIndex:slot.unsignedIntegerValue];
            restoreBuffer(e,state,YES,state.vertexDrawSlot);restoreBuffer(e,state,NO,state.fragmentDrawSlot);
        }
        @finally { state.busy = NO; }
        [state.overridden removeAllObjects]; [state.views removeAllObjects]; NSDictionary *info=objc_getAssociatedObject(ps,&pipelineKey);
        state.bindings=info[@"bindings"];state.vertexDrawSlot=info?[info[@"vertex"] integerValue]:-1;state.fragmentDrawSlot=info?[info[@"fragment"] integerValue]:-1;
        ((void(*)(id,SEL,id))p)(e,s,ps);
    });
    SEL direct = @selector(drawPrimitives:vertexStart:vertexCount:instanceCount:baseInstance:);
    IMP directIMP = class_getMethodImplementation(c,direct);
    void (^drawDirect)(id,MTLPrimitiveType,NSUInteger,NSUInteger,NSUInteger,NSUInteger) =
        ^(id e,MTLPrimitiveType type,NSUInteger start,NSUInteger count,NSUInteger instances,NSUInteger base) {
        FBState *state = objc_getAssociatedObject(e,&encoderKey);
        if (!state.bindings.count || state.busy) {
            ((void(*)(id,SEL,NSUInteger,NSUInteger,NSUInteger,NSUInteger,NSUInteger))directIMP)(e,direct,type,start,count,instances,base); return;
        }
        NSUInteger size = width(type);
        state.busy = YES;
        @try {
            for (NSUInteger instance=0;instance<instances;++instance)
                for (NSUInteger primitive=0;primitive<primitiveCount(type,count);++primitive) {
                    prepare(e,state,start,base,primitive);
                    if(type==MTLPrimitiveTypeTriangleStrip)[e setFrontFacingWinding:state.winding^(primitive&1)];
                    ((void(*)(id,SEL,NSUInteger,NSUInteger,NSUInteger,NSUInteger,NSUInteger))directIMP)(e,direct,type,start+primitive*(strip(type)?1:size),size,1,base+instance);
                }
        } @finally { if(type==MTLPrimitiveTypeTriangleStrip)[e setFrontFacingWinding:state.winding];state.busy = NO; }
    };
    hook(c,direct,^(id e,NSUInteger a,NSUInteger b,NSUInteger n,NSUInteger i,NSUInteger base){drawDirect(e,a,b,n,i,base);});
    s=@selector(drawPrimitives:vertexStart:vertexCount:instanceCount:); p=class_getMethodImplementation(c,s);
    hook(c,s,^(id e,NSUInteger a,NSUInteger b,NSUInteger n,NSUInteger i){
        FBState *state=objc_getAssociatedObject(e,&encoderKey);
        if (state.bindings.count && !state.busy) drawDirect(e,a,b,n,i,0);
        else ((void(*)(id,SEL,NSUInteger,NSUInteger,NSUInteger,NSUInteger))p)(e,s,a,b,n,i);
    });
    s=@selector(drawPrimitives:vertexStart:vertexCount:); p=class_getMethodImplementation(c,s);
    hook(c,s,^(id e,NSUInteger a,NSUInteger b,NSUInteger n){
        FBState *state=objc_getAssociatedObject(e,&encoderKey);
        if (state.bindings.count && !state.busy) drawDirect(e,a,b,n,1,0);
        else ((void(*)(id,SEL,NSUInteger,NSUInteger,NSUInteger))p)(e,s,a,b,n);
    });
    SEL indexed=@selector(drawIndexedPrimitives:indexCount:indexType:indexBuffer:indexBufferOffset:instanceCount:baseVertex:baseInstance:);
    IMP indexedIMP=class_getMethodImplementation(c,indexed);
    void (^drawIndexed)(id,MTLPrimitiveType,NSUInteger,MTLIndexType,id,NSUInteger,NSUInteger,NSInteger,NSUInteger)=
        ^(id e,MTLPrimitiveType type,NSUInteger count,MTLIndexType it,id buffer,NSUInteger offset,NSUInteger instances,NSInteger vertex,NSUInteger base) {
        FBState *state=objc_getAssociatedObject(e,&encoderKey);
        if (!state.bindings.count || state.busy) {
            ((void(*)(id,SEL,NSUInteger,NSUInteger,NSUInteger,id,NSUInteger,NSUInteger,NSInteger,NSUInteger))indexedIMP)(e,indexed,type,count,it,buffer,offset,instances,vertex,base); return;
        }
        NSUInteger size=width(type),stride=it==MTLIndexTypeUInt16?2:4;
        state.busy=YES;
        @try {
            for(NSUInteger instance=0;instance<instances;++instance)
                for(NSUInteger primitive=0;primitive<primitiveCount(type,count);++primitive) {
                    prepare(e,state,vertex,base,primitive);
                    if(type==MTLPrimitiveTypeTriangleStrip)[e setFrontFacingWinding:state.winding^(primitive&1)];
                    ((void(*)(id,SEL,NSUInteger,NSUInteger,NSUInteger,id,NSUInteger,NSUInteger,NSInteger,NSUInteger))indexedIMP)(e,indexed,type,size,it,buffer,offset+primitive*(strip(type)?1:size)*stride,1,vertex,base+instance);
                }
        } @finally {if(type==MTLPrimitiveTypeTriangleStrip)[e setFrontFacingWinding:state.winding];state.busy=NO;}
    };
    hook(c,indexed,^(id e,NSUInteger a,NSUInteger n,NSUInteger t,id b,NSUInteger o,NSUInteger i,NSInteger v,NSUInteger base){drawIndexed(e,a,n,t,b,o,i,v,base);});
    s=@selector(drawIndexedPrimitives:indexCount:indexType:indexBuffer:indexBufferOffset:instanceCount:);p=class_getMethodImplementation(c,s);
    hook(c,s,^(id e,NSUInteger a,NSUInteger n,NSUInteger t,id b,NSUInteger o,NSUInteger i){
        FBState *state=objc_getAssociatedObject(e,&encoderKey);
        if(state.bindings.count && !state.busy)drawIndexed(e,a,n,t,b,o,i,0,0);
        else ((void(*)(id,SEL,NSUInteger,NSUInteger,NSUInteger,id,NSUInteger,NSUInteger))p)(e,s,a,n,t,b,o,i);
    });
    s=@selector(drawIndexedPrimitives:indexCount:indexType:indexBuffer:indexBufferOffset:);p=class_getMethodImplementation(c,s);
    hook(c,s,^(id e,NSUInteger a,NSUInteger n,NSUInteger t,id b,NSUInteger o){
        FBState *state=objc_getAssociatedObject(e,&encoderKey);
        if(state.bindings.count && !state.busy)drawIndexed(e,a,n,t,b,o,1,0,0);
        else ((void(*)(id,SEL,NSUInteger,NSUInteger,NSUInteger,id,NSUInteger))p)(e,s,a,n,t,b,o);
    });
    // Do not silently execute a lowered shader with unbound attachments.
    s=@selector(drawPrimitives:indirectBuffer:indirectBufferOffset:);p=class_getMethodImplementation(c,s);
    hook(c,s,^(id e,NSUInteger a,id b,NSUInteger o){
        FBState *state=objc_getAssociatedObject(e,&encoderKey);
        if(state.bindings.count)fail(@"Indirect draws need GPU-side primitive expansion");
        ((void(*)(id,SEL,NSUInteger,id,NSUInteger))p)(e,s,a,b,o);
    });
    s=@selector(drawIndexedPrimitives:indexType:indexBuffer:indexBufferOffset:indirectBuffer:indirectBufferOffset:);p=class_getMethodImplementation(c,s);
    hook(c,s,^(id e,NSUInteger a,NSUInteger t,id b,NSUInteger o,id indirect,NSUInteger io){
        FBState *state=objc_getAssociatedObject(e,&encoderKey);
        if(state.bindings.count)fail(@"Indirect indexed draws need GPU-side primitive expansion");
        ((void(*)(id,SEL,NSUInteger,NSUInteger,id,NSUInteger,id,NSUInteger))p)(e,s,a,t,b,o,indirect,io);
    });
}}

static FBState *trackEncoder(id e,MTLRenderPassDescriptor *desc) {
    if(!e)return nil;
    encoderHooks(object_getClass(e));FBState *state=[FBState new];state.pass=[desc copy];
    objc_setAssociatedObject(e,&encoderKey,state,OBJC_ASSOCIATION_RETAIN_NONATOMIC);return state;
}
static void parallelHooks(Class c) { @synchronized(FBState.class) {
    if(objc_getAssociatedObject(c,&parallelHooksKey))return;
    objc_setAssociatedObject(c,&parallelHooksKey,@YES,OBJC_ASSOCIATION_RETAIN_NONATOMIC);
    SEL selector=@selector(renderCommandEncoder);IMP original=class_getMethodImplementation(c,selector);
    hook(c,selector,^id(id parent) {
        id e=((id(*)(id,SEL))original)(parent,selector);if(!e)return nil;
        NSDictionary *context=objc_getAssociatedObject(parent,&parallelKey);
        FBState *state=trackEncoder(e,context[@"pass"]);NSMutableArray *lifetime=context[@"lifetime"];
        @synchronized(lifetime){[lifetime addObject:state];}return e;
    });
}}

static void commandHooks(Class c) { @synchronized(FBState.class) {
    if(!c || objc_getAssociatedObject(c,&commandHooksKey))return;
    objc_setAssociatedObject(c,&commandHooksKey,@YES,OBJC_ASSOCIATION_RETAIN_NONATOMIC);
    SEL s=@selector(renderCommandEncoderWithDescriptor:);IMP p=class_getMethodImplementation(c,s);
    hook(c,s,^id(id command,MTLRenderPassDescriptor *desc){
        id e=((id(*)(id,SEL,id))p)(command,s,desc);if(!e)return nil;
        FBState *state=trackEncoder(e,desc);
        // The command buffer can be unretained and the encoder can die before commit.
        // Keep generated views and pass attachments until the GPU has finished.
        [command addCompletedHandler:^(id<MTLCommandBuffer> finished){(void)finished;(void)state.resources;}];
        return e;
    });
    s=@selector(parallelRenderCommandEncoderWithDescriptor:);p=class_getMethodImplementation(c,s);
    hook(c,s,^id(id command,MTLRenderPassDescriptor *desc) {
        id parent=((id(*)(id,SEL,id))p)(command,s,desc);if(!parent)return nil;
        parallelHooks(object_getClass(parent));NSMutableArray *lifetime=[NSMutableArray new];
        objc_setAssociatedObject(parent,&parallelKey,@{@"pass":[desc copy],@"lifetime":lifetime},OBJC_ASSOCIATION_RETAIN_NONATOMIC);
        [command addCompletedHandler:^(id<MTLCommandBuffer> finished){(void)finished;(void)lifetime.count;}];return parent;
    });
}}

static void queueHooks(Class c) { @synchronized(FBState.class) {
    if(!c || objc_getAssociatedObject(c,&queueHooksKey))return;
    objc_setAssociatedObject(c,&queueHooksKey,@YES,OBJC_ASSOCIATION_RETAIN_NONATOMIC);
    for(NSString *name in @[@"commandBuffer",@"commandBufferWithUnretainedReferences"]) {
        SEL selector=NSSelectorFromString(name);IMP original=class_getMethodImplementation(c,selector);
        hook(c,selector,^id(id queue){id cb=((id(*)(id,SEL))original)(queue,selector);if(cb)commandHooks(object_getClass(cb));return cb;});
    }
    SEL selector=@selector(commandBufferWithDescriptor:);
    if(class_getInstanceMethod(c,selector)) {
        IMP original=class_getMethodImplementation(c,selector);
        hook(c,selector,^id(id queue,id desc){id cb=((id(*)(id,SEL,id))original)(queue,selector,desc);if(cb)commandHooks(object_getClass(cb));return cb;});
    }
}}
static void deviceHooks(Class c) { @synchronized(FBState.class) {
    if(!c || objc_getAssociatedObject(c,&deviceHooksKey))return;
    objc_setAssociatedObject(c,&deviceHooksKey,@YES,OBJC_ASSOCIATION_RETAIN_NONATOMIC);
    SEL options=@selector(newRenderPipelineStateWithDescriptor:options:reflection:error:);
    IMP optionsIMP=class_getMethodImplementation(c,options);
    id (^create)(id,id,NSUInteger,id *,NSError **)=^id(id d,id desc,NSUInteger flags,id *reflection,NSError **error){
        MTLRenderPipelineDescriptor *lowered=[desc copy];
        lowered.fragmentFunction=FBFramebufferFunction(lowered.fragmentFunction,lowered.rasterSampleCount,error);
        if([desc fragmentFunction] && !lowered.fragmentFunction)return nil;
        if(lowered.fragmentFunction!=[desc fragmentFunction]) {
            lowered.vertexFunction=FBFramebufferVertexFunction(lowered.vertexFunction,error);
            if([desc vertexFunction] && !lowered.vertexFunction)return nil;
        }
        MTLRenderPipelineReflection *internal=nil;
        id ps=((FBNewPipeline)optionsIMP)(d,options,lowered,flags|MTLPipelineOptionArgumentInfo,&internal,error);
        record(ps,internal);if(reflection)*reflection=internal;return ps;
    };
    hook(c,options,^id(id d,id desc,NSUInteger flags,id *reflection,NSError **error) __attribute__((ns_returns_retained)) {return create(d,desc,flags,reflection,error);});
    SEL s=@selector(newRenderPipelineStateWithDescriptor:error:);
    hook(c,s,^id(id d,id desc,NSError **error) __attribute__((ns_returns_retained)) {return create(d,desc,0,NULL,error);});
    s=@selector(newRenderPipelineStateWithDescriptor:completionHandler:);
    hook(c,s,^(id d,MTLRenderPipelineDescriptor *desc,void (^completion)(id,NSError *)) {
        MTLRenderPipelineDescriptor *copy=[desc copy];
        dispatch_async(dispatch_get_global_queue(QOS_CLASS_DEFAULT,0),^{
            NSError *error=nil;id ps=create(d,copy,0,NULL,&error);completion(ps,error);
        });
    });
    s=@selector(newRenderPipelineStateWithDescriptor:options:completionHandler:);
    hook(c,s,^(id d,MTLRenderPipelineDescriptor *desc,NSUInteger flags,void (^completion)(id,id,NSError *)) {
        MTLRenderPipelineDescriptor *copy=[desc copy];
        dispatch_async(dispatch_get_global_queue(QOS_CLASS_DEFAULT,0),^{
            NSError *error=nil;id reflection=nil;id ps=create(d,copy,flags,&reflection,&error);completion(ps,reflection,error);
        });
    });

    s=@selector(newCommandQueue);IMP p=class_getMethodImplementation(c,s);
    hook(c,s,^id(id device) __attribute__((ns_returns_retained)) {id queue=((FBNewQueue)p)(device,s);if(queue)queueHooks(object_getClass(queue));return queue;});
    s=@selector(newCommandQueueWithMaxCommandBufferCount:);p=class_getMethodImplementation(c,s);
    hook(c,s,^id(id device,NSUInteger count) __attribute__((ns_returns_retained)) {id queue=((FBNewQueueCount)p)(device,s,count);if(queue)queueHooks(object_getClass(queue));return queue;});
}}
__attribute__((constructor)) static void install(void) { @autoreleasepool {
#if FB_SYSTEM
    // Loaded as a dependency of this legacy driver. Do not create a device from
    // its loader: queues and command buffers are attached when clients make them.
    deviceHooks(NSClassFromString(@"MTLIGAccelDevice"));
#else
    if(!getenv("METAL_FRAMEBUFFER_PROBE"))return;
    id<MTLDevice> device=MTLCreateSystemDefaultDevice();if(device)deviceHooks(object_getClass(device));
#endif
}}
