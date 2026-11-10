#import <Metal/Metal.h>
#ifdef __cplusplus
extern "C" {
#endif
// Called before the driver sees a render descriptor or forms its pipeline keys.
id<MTLFunction> FBFramebufferFunction(id<MTLFunction> function, NSUInteger samples, NSError **error);
id<MTLFunction> FBFramebufferVertexFunction(id<MTLFunction> function, NSError **error);
#ifdef __cplusplus
}
#endif
