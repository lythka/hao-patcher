#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
int main(int argc, const char **argv) { @autoreleasepool {
 if(argc!=5) return 2;
 NSError *e=nil; id<MTLDevice> d=MTLCreateSystemDefaultDevice();
 id<MTLLibrary> lib=[d newLibraryWithURL:[NSURL fileURLWithPath:@(argv[1])] error:&e];
 if(!lib) {fprintf(stderr,"library: %s\n",e.description.UTF8String);return 3;}
 id<MTLComputePipelineState> p=[d newComputePipelineStateWithFunction:[lib newFunctionWithName:@"probe"] error:&e];
 if(!p) {fprintf(stderr,"pipeline: %s\n",e.description.UTF8String);return 4;}
 NSData *input=[NSData dataWithContentsOfFile:@(argv[2])], *masks=[NSData dataWithContentsOfFile:@(argv[3])];
 NSUInteger width=p.threadExecutionWidth, groups=masks.length/4;
 if(!width || width>32 || input.length<groups*width*16) return 5;
 id<MTLBuffer> a=[d newBufferWithBytes:input.bytes length:input.length options:MTLResourceStorageModeShared];
 id<MTLBuffer> b=[d newBufferWithLength:input.length options:MTLResourceStorageModeShared];
 id<MTLBuffer> m=[d newBufferWithBytes:masks.bytes length:masks.length options:MTLResourceStorageModeShared];
 memset(b.contents,0xcd,input.length);
 id<MTLCommandQueue> q=[d newCommandQueue];id<MTLCommandBuffer> cb=[q commandBuffer];
 id<MTLComputeCommandEncoder> ce=[cb computeCommandEncoder];[ce setComputePipelineState:p];
 [ce setBuffer:a offset:0 atIndex:0];[ce setBuffer:b offset:0 atIndex:1];[ce setBuffer:m offset:0 atIndex:2];
 [ce dispatchThreadgroups:MTLSizeMake(groups,1,1) threadsPerThreadgroup:MTLSizeMake(width,1,1)];
 [ce endEncoding];[cb commit];[cb waitUntilCompleted];
 if(cb.status!=MTLCommandBufferStatusCompleted) {fprintf(stderr,"execution: %s\n",cb.error.description.UTF8String);return 6;}
 [[NSData dataWithBytes:b.contents length:groups*width*16] writeToFile:@(argv[4]) atomically:YES];
 printf("{\"device\":\"%s\",\"width\":%lu,\"groups\":%lu}\n",d.name.UTF8String,(unsigned long)width,(unsigned long)groups);
 return 0;
}}
