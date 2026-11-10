#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
int main(int argc, const char **argv) { @autoreleasepool {
 if(argc!=7) { fprintf(stderr,"probe LIB A B C OUT GROUPS\n"); return 2; }
 id<MTLDevice> d=MTLCreateSystemDefaultDevice();NSError *e=nil;
 fprintf(stderr,"device=%s\n",d.name.UTF8String);
 id<MTLLibrary> lib=[d newLibraryWithURL:[NSURL fileURLWithPath:@(argv[1])] error:&e];
 if(!lib) {fprintf(stderr,"library: %s\n",e.description.UTF8String);return 3;}
 id<MTLComputePipelineState> p=[d newComputePipelineStateWithFunction:[lib newFunctionWithName:@"probe"] error:&e];
 if(!p) {fprintf(stderr,"pipeline: %s\n",e.description.UTF8String);return 4;}
 unsigned groups=(unsigned)strtoul(argv[6],NULL,10);NSUInteger bytes=groups*32*2*sizeof(float);
 id<MTLBuffer> b[4];for(int i=0;i<3;i++) {NSData *data=[NSData dataWithContentsOfFile:@(argv[2+i])];if(data.length!=bytes)return 5;b[i]=[d newBufferWithBytes:data.bytes length:bytes options:MTLResourceStorageModeShared];}
 b[3]=[d newBufferWithLength:bytes options:MTLResourceStorageModeShared];memset(b[3].contents,0xcd,bytes);
 id<MTLCommandQueue> q=[d newCommandQueue];id<MTLCommandBuffer> cb=[q commandBuffer];id<MTLComputeCommandEncoder> ce=[cb computeCommandEncoder];[ce setComputePipelineState:p];[ce setThreadgroupMemoryLength:1024 atIndex:0];for(int i=0;i<4;i++)[ce setBuffer:b[i] offset:0 atIndex:i];[ce dispatchThreadgroups:MTLSizeMake(groups,1,1) threadsPerThreadgroup:MTLSizeMake(32,1,1)];[ce endEncoding];[cb commit];[cb waitUntilCompleted];
 if(cb.status!=MTLCommandBufferStatusCompleted) {fprintf(stderr,"execution: %s\n",cb.error.description.UTF8String);return 6;}
 [[NSData dataWithBytes:b[3].contents length:bytes] writeToFile:@(argv[5]) atomically:YES];fprintf(stderr,"width=%lu groups=%u OK\n",(unsigned long)p.threadExecutionWidth,groups);return 0;
}}
