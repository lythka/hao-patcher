#import <Foundation/Foundation.h>
extern void LiveTextLibraryTestObserve(id);
@interface BaseDevice:NSObject
@property NSString *name;
@property NSString *lastPath;
- (id)newLibraryWithFile:(NSString *)path error:(NSError **)error;
@end
@implementation BaseDevice
- (id)newLibraryWithFile:(NSString *)path error:(NSError **)error {
    self.lastPath=path;
    if([path containsString:@"missing"]){if(error)*error=[NSError errorWithDomain:@"Authored" code:7 userInfo:nil];return nil;}
    return [[NSObject alloc]init];
}
@end
@interface IntelDevice:BaseDevice @end
@implementation IntelDevice @end
@interface OtherDevice:BaseDevice @end
@implementation OtherDevice @end
int main(void){@autoreleasepool{
    NSString *original=@"/System/Library/Frameworks/MetalPerformanceShaders.framework/Versions/A/Frameworks/MPSNDArray.framework/Versions/A/Resources/default.metallib";
    IntelDevice *intel=[IntelDevice new];intel.name=@"Intel HD Graphics 4000";
    OtherDevice *other=[OtherDevice new];other.name=@"Other GPU";
    setenv("VISION_MPSNDARRAY_LIBRARY","/Library/hao-live-text/test/MPSNDArray.metallib",1);
    LiveTextLibraryTestObserve(nil);LiveTextLibraryTestObserve(other);
    [other newLibraryWithFile:original error:nil];NSCAssert([other.lastPath isEqual:original],@"unrelated device changed");
    LiveTextLibraryTestObserve(intel);LiveTextLibraryTestObserve(intel);
    [intel newLibraryWithFile:original error:nil];NSCAssert([intel.lastPath isEqual:@"/Library/hao-live-text/test/MPSNDArray.metallib"],@"candidate not selected");
    [intel newLibraryWithFile:@"/other/default.metallib" error:nil];NSCAssert([intel.lastPath isEqual:@"/other/default.metallib"],@"unrelated library changed");
    [other newLibraryWithFile:original error:nil];NSCAssert([other.lastPath isEqual:original],@"base class changed");
    IntelDevice *sameClassOther=[IntelDevice new];sameClassOther.name=@"Other GPU";
    [sameClassOther newLibraryWithFile:original error:nil];NSCAssert([sameClassOther.lastPath isEqual:original],@"same-class other device changed");
    setenv("VISION_MPSNDARRAY_LIBRARY","/missing/MPSNDArray.metallib",1);NSError *error=nil;
    id result=[intel newLibraryWithFile:original error:&error];NSCAssert(!result&&error.code==7,@"missing file did not preserve API error");
    unsetenv("VISION_MPSNDARRAY_LIBRARY");[intel newLibraryWithFile:original error:nil];NSCAssert([intel.lastPath isEqual:original],@"disabled path changed");
    puts("lazy resource controls passed");return 0;
}}
