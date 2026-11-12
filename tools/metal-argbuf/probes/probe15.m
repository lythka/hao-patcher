// probe15.m FILE.metal FUNCTION BUFFERINDEX: dump MetalOld's reflection of an argument-buffer argument (member offsets,
// types, argument indices, nested structs/arrays/pointers) and, with libargbuf, the encoder's length and alignment.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
static void dumpStruct(MTLStructType *st, int depth);
static void dumpType(MTLDataType t, id info, int depth) {
    if (t == MTLDataTypeStruct) { printf(" struct\n"); dumpStruct(info, depth + 1); return; }
    printf("\n");
}
static void dumpMember(MTLStructMember *m, int depth) {
    printf("%*s%-12s off %4ld type %3lu argIndex %4lu", depth * 2, "", m.name.UTF8String, (long)m.offset, (unsigned long)m.dataType,
           (unsigned long)m.argumentIndex);
    if (m.dataType == MTLDataTypeArray) {
        MTLArrayType *a = m.arrayType;
        printf(" array len %lu stride %lu elem %lu argIndexStride %lu", (unsigned long)a.arrayLength, (unsigned long)a.stride,
               (unsigned long)a.elementType, (unsigned long)a.argumentIndexStride);
        dumpType(a.elementType, a.elementType == MTLDataTypeStruct ? a.elementStructType : nil, depth);
    } else if (m.dataType == MTLDataTypePointer) {
        MTLPointerType *p = m.pointerType;
        printf(" ptr elem %lu isAB %d", (unsigned long)p.elementType, p.elementIsArgumentBuffer);
        dumpType(p.elementType, p.elementType == MTLDataTypeStruct ? p.elementStructType : nil, depth);
    } else if (m.dataType == MTLDataTypeStruct) {
        dumpType(MTLDataTypeStruct, m.structType, depth);
    } else printf("\n");
}
static void dumpStruct(MTLStructType *st, int depth) { for (MTLStructMember *m in st.members) dumpMember(m, depth); }
int main(int argc, char **argv) { @autoreleasepool {
    if (argc != 4) { fprintf(stderr, "usage: probe15 FILE.metal FUNCTION BUFFERINDEX\n"); return 2; }
    id<MTLDevice> d = MTLCreateSystemDefaultDevice();
    NSError *e = nil; NSString *src = [NSString stringWithContentsOfFile:@(argv[1]) encoding:NSUTF8StringEncoding error:&e];
    id<MTLLibrary> lib = src ? [d newLibraryWithSource:src options:nil error:&e] : nil;
    if (!lib) { printf("library FAILED %s\n", e.localizedDescription.UTF8String); return 1; }
    id<MTLFunction> f = [lib newFunctionWithName:@(argv[2])];
    NSUInteger bi = (NSUInteger)atoi(argv[3]);
    MTLComputePipelineReflection *r = nil;
    id<MTLComputePipelineState> p = [d newComputePipelineStateWithFunction:f options:MTLPipelineOptionArgumentInfo | MTLPipelineOptionBufferTypeInfo
                                                                reflection:&r error:&e];
    printf("pipeline %s %s\n", p ? "OK" : "FAILED", p ? "" : e.localizedDescription.UTF8String);
    for (MTLArgument *a in r.arguments) if (a.type == MTLArgumentTypeBuffer && a.index == bi && a.bufferStructType) dumpStruct(a.bufferStructType, 1);
    id<MTLArgumentEncoder> enc = [f newArgumentEncoderWithBufferIndex:bi];
    printf("encoder %s encodedLength %lu alignment %lu\n", enc ? object_getClassName(enc) : "nil", (unsigned long)enc.encodedLength, (unsigned long)enc.alignment);
    return 0;
} }
