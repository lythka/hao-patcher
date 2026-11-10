#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
int main(int argc,const char **argv) { @autoreleasepool {
 if(argc!=4 && argc!=5)return 2;
 id<MTLDevice>d=MTLCreateSystemDefaultDevice();NSError*e=nil;
 id<MTLLibrary>l=[d newLibraryWithURL:[NSURL fileURLWithPath:@(argv[1])] error:&e];
 if(!l){fprintf(stderr,"library: %s\n",e.description.UTF8String);return 3;}
 id<MTLComputePipelineState>p=[d newComputePipelineStateWithFunction:[l newFunctionWithName:@"probe"] error:&e];
 if(!p){fprintf(stderr,"pipeline: %s\n",e.description.UTF8String);return 4;}
 NSData*data=[NSData dataWithContentsOfFile:@(argv[2])];NSUInteger n=data.length/16;
 id<MTLBuffer>coords=[d newBufferWithBytes:data.bytes length:data.length options:MTLResourceStorageModeShared];
 id<MTLBuffer>out=[d newBufferWithLength:n*32 options:MTLResourceStorageModeShared];
 MTLTextureDescriptor*td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:9 height:7 mipmapped:YES];
 td.storageMode=MTLStorageModeShared;td.usage=MTLTextureUsageShaderRead;td.textureType=MTLTextureType2DArray;td.arrayLength=3;
 id<MTLTexture>t=[d newTextureWithDescriptor:td];
 for(unsigned layer=0;layer<3;layer++)for(unsigned mip=0;mip<td.mipmapLevelCount;mip++){
  unsigned w=MAX(1,9>>mip),h=MAX(1,7>>mip);float pixels[9*7*4];
  for(unsigned y=0;y<h;y++)for(unsigned x=0;x<w;x++)for(unsigned c=0;c<4;c++)
   pixels[(y*w+x)*4+c]=(float)((int)(mip*10000+layer*1000+y*37+x*7+c)-300)/997.0f;
  [t replaceRegion:MTLRegionMake2D(0,0,w,h) mipmapLevel:mip slice:layer withBytes:pixels bytesPerRow:w*16 bytesPerImage:w*h*16];
 }
 id<MTLCommandQueue>q=[d newCommandQueue];id<MTLCommandBuffer>b=[q commandBuffer];id<MTLComputeCommandEncoder>ce=[b computeCommandEncoder];
 [ce setComputePipelineState:p];[ce setTexture:t atIndex:0];[ce setBuffer:coords offset:0 atIndex:0];[ce setBuffer:out offset:0 atIndex:1];
 if(argc==5){
  NSDictionary *s=[NSJSONSerialization JSONObjectWithData:[NSData dataWithContentsOfFile:@(argv[4])] options:0 error:&e];
  if(!s){fprintf(stderr,"sampler descriptor: %s\n",e.description.UTF8String);return 6;}
  for(unsigned binding=0;binding<2;binding++){
  NSDictionary *settings=binding?s[@"secondary"]:s;if(!settings)continue;
  MTLSamplerDescriptor *sd=[MTLSamplerDescriptor new];
  sd.normalizedCoordinates=[settings[@"normalized"] boolValue];
  sd.sAddressMode=[settings[@"address"] unsignedIntegerValue];
  sd.tAddressMode=[settings[@"addressT"] ?: settings[@"address"] unsignedIntegerValue];
  sd.minFilter=sd.magFilter=[settings[@"filter"] unsignedIntegerValue];
  sd.mipFilter=[settings[@"mip"] unsignedIntegerValue];
  id<MTLSamplerState>sampler=[d newSamplerStateWithDescriptor:sd];
  if(!sampler){fprintf(stderr,"sampler creation failed\n");return 6;}
  [ce setSamplerState:sampler atIndex:binding?3:0];
  }
 }
 [ce dispatchThreadgroups:MTLSizeMake(n/32,1,1) threadsPerThreadgroup:MTLSizeMake(32,1,1)];[ce endEncoding];[b commit];[b waitUntilCompleted];
 if(b.status!=MTLCommandBufferStatusCompleted){fprintf(stderr,"execution: %s\n",b.error.description.UTF8String);return 5;}
 [[NSData dataWithBytes:out.contents length:n*32] writeToFile:@(argv[3]) atomically:YES];
 printf("{\"device\":\"%s\",\"width\":%lu,\"samples\":%lu}\n",d.name.UTF8String,(unsigned long)p.threadExecutionWidth,(unsigned long)(n*2));return 0;
}}
