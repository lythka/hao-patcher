// probe9.m: argument-buffer struct layouts on MetalOld (HD 4000). No encoder is created (that aborts today).
// 1) MTLCreateStructTypeFromArgumentDescriptors (what newArgumentEncoderWithArguments: hands the device layout)
// 2) reflection bufferStructType of a `constant AB&` kernel argument (the front-end layout the IGIL target reads).
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <dlfcn.h>
static MTLStructType *(*MTLCreateStructTypeFromArgumentDescriptors)(NSArray<MTLArgumentDescriptor *> *);
static void dumpStruct(MTLStructType *st, int depth);
static void dumpMember(MTLStructMember *m, int depth) {
    printf("%*s%-10s off %3lu type %3lu argIndex %3lu", depth * 2, "", m.name.UTF8String, (unsigned long)m.offset,
           (unsigned long)m.dataType, (unsigned long)m.argumentIndex);
    if (m.dataType == MTLDataTypeArray) {
        MTLArrayType *a = m.arrayType;
        printf(" array len %lu stride %lu elem %lu argIndexStride %lu\n", (unsigned long)a.arrayLength, (unsigned long)a.stride,
               (unsigned long)a.elementType, (unsigned long)a.argumentIndexStride);
        if (a.elementType == MTLDataTypeStruct) dumpStruct(a.elementStructType, depth + 1);
    } else if (m.dataType == MTLDataTypePointer) {
        MTLPointerType *p = m.pointerType;
        printf(" ptr elem %lu size %lu align %lu isAB %d\n", (unsigned long)p.elementType, (unsigned long)p.dataSize,
               (unsigned long)p.alignment, p.elementIsArgumentBuffer);
        if (p.elementType == MTLDataTypeStruct) dumpStruct(p.elementStructType, depth + 1);
    } else if (m.dataType == MTLDataTypeTexture) {
        MTLTextureReferenceType *t = m.textureReferenceType;
        printf(" tex type %lu data %lu depth %d\n", (unsigned long)t.textureType, (unsigned long)t.textureDataType, t.isDepthTexture);
    } else if (m.dataType == MTLDataTypeStruct) {
        printf(" struct\n"); dumpStruct(m.structType, depth + 1);
    } else printf("\n");
}
static void dumpStruct(MTLStructType *st, int depth) { for (MTLStructMember *m in st.members) dumpMember(m, depth); }
static MTLArgumentDescriptor *D(MTLDataType t, NSUInteger idx, NSUInteger len, MTLTextureType tt) {
    MTLArgumentDescriptor *a = [MTLArgumentDescriptor argumentDescriptor];
    a.dataType = t; a.index = idx; a.arrayLength = len; a.textureType = tt; a.access = MTLBindingAccessReadOnly; return a;
}
static NSString *src = @
"#include <metal_stdlib>\n"
"using namespace metal;\n"
"struct Inner { texture2d<float> a [[id(0)]]; constant float4 *b [[id(1)]]; };\n"
"struct AB { texture2d<half> t0 [[id(11)]]; constant float4 *p [[id(12)]]; texture3d<float> t3 [[id(13)]];\n"
"            array<texture2d<float>, 3> arr [[id(14)]]; constant Inner &in [[id(20)]]; uint k [[id(21)]];\n"
"            float4 v [[id(22)]]; constant float *q [[id(23)]]; };\n"
"kernel void kab(constant AB &ab [[buffer(19)]], device float4 *out [[buffer(1)]], uint gid [[thread_position_in_grid]])\n"
"{ out[gid] = ab.p[0] + ab.in.b[0] + float4(ab.k) + ab.v + ab.q[0] + float4(ab.t0.read(uint2(0)))\n"
"            + ab.t3.read(uint3(0)) + ab.arr[gid & 1].read(uint2(0)) + ab.in.a.read(uint2(0)); }\n";
int main(void) { @autoreleasepool {
    id<MTLDevice> d = MTLCreateSystemDefaultDevice();
    MTLCreateStructTypeFromArgumentDescriptors = dlsym(RTLD_DEFAULT, "MTLCreateStructTypeFromArgumentDescriptors");
    if (!MTLCreateStructTypeFromArgumentDescriptors) { printf("no MTLCreateStructTypeFromArgumentDescriptors\n"); return 1; }
    printf("device %s class %s argumentBuffersSupport %lu\n", d.name.UTF8String, object_getClassName(d), (unsigned long)d.argumentBuffersSupport);
    NSArray *desc = @[ D(MTLDataTypeTexture, 11, 0, MTLTextureType2D), D(MTLDataTypePointer, 12, 0, MTLTextureType2D),
                       D(MTLDataTypeTexture, 13, 0, MTLTextureType3D), D(MTLDataTypeTexture, 14, 3, MTLTextureType2D),
                       D(MTLDataTypePointer, 20, 0, MTLTextureType2D), D(MTLDataTypeUInt, 21, 0, MTLTextureType2D),
                       D(MTLDataTypeFloat4, 22, 0, MTLTextureType2D), D(MTLDataTypePointer, 23, 0, MTLTextureType2D),
                       D(MTLDataTypeSampler, 30, 0, MTLTextureType2D) ];
    MTLStructType *st = MTLCreateStructTypeFromArgumentDescriptors(desc);
    printf("== descriptors -> struct type (%s)\n", object_getClassName(st)); dumpStruct(st, 1);
    NSError *e = nil; id<MTLLibrary> lib = [d newLibraryWithSource:src options:nil error:&e];
    if (!lib) { printf("library FAILED %s\n", e.localizedDescription.UTF8String); return 1; }
    MTLComputePipelineReflection *r = nil;
    id<MTLComputePipelineState> p = [d newComputePipelineStateWithFunction:[lib newFunctionWithName:@"kab"]
        options:MTLPipelineOptionArgumentInfo | MTLPipelineOptionBufferTypeInfo reflection:&r error:&e];
    printf("== kab pipeline %s %s\n", p ? "OK" : "FAILED", e ? e.localizedDescription.UTF8String : "");
    for (MTLArgument *a in r.arguments) {
        printf("arg %s type %lu index %lu", a.name.UTF8String, (unsigned long)a.type, (unsigned long)a.index);
        if (a.type == MTLArgumentTypeBuffer) {
            printf(" bufferDataType %lu size %lu align %lu\n", (unsigned long)a.bufferDataType, (unsigned long)a.bufferDataSize, (unsigned long)a.bufferAlignment);
            if (a.bufferStructType) dumpStruct(a.bufferStructType, 1);
        } else printf("\n");
    }
    return 0;
} }
