/* Target-only identity observation; does not register items or alter decisions.
 * Compile with Foundation, sign, and run on the test image, optionally with the
 * identity shim in DYLD_INSERT_LIBRARIES. Never launch this against the host's
 * private framework: its version is not the image under test.
 */
#import <Foundation/Foundation.h>
#include <dlfcn.h>

@interface NSObject (BTMProbe)
+ (id)codeSignInfoForURL:(NSURL *)url error:(NSError **)error;
- (BOOL)isSystemApp;
- (NSString *)bundleIdentifier;
- (NSString *)signingIdentifier;
- (NSString *)developerName;
- (NSString *)teamIdentifier;
@end

int main(int argc, const char **argv) { @autoreleasepool {
    if (argc < 2) { fprintf(stderr, "usage: btm-probe PATH...\n"); return 2; }
    void *handle = dlopen("/System/Library/PrivateFrameworks/BackgroundTaskManagement.framework/BackgroundTaskManagement", RTLD_NOW);
    if (!handle) { fprintf(stderr, "%s\n", dlerror()); return 1; }
    Class cls = NSClassFromString(@"BTMCodeSignInfo");
    if (!cls || ![cls respondsToSelector:@selector(codeSignInfoForURL:error:)]) return 1;
    NSMutableArray *rows = [NSMutableArray array];
    for (int i = 1; i < argc; i++) {
        NSString *path = [NSString stringWithUTF8String:argv[i]];
        NSError *error = nil;
        id info = [cls codeSignInfoForURL:[NSURL fileURLWithPath:path] error:&error];
        [rows addObject:@{
            @"path": path,
            @"error": error.description ?: (id)[NSNull null],
            @"isSystemApp": @([info isSystemApp]),
            @"bundleIdentifier": [info bundleIdentifier] ?: (id)[NSNull null],
            @"signingIdentifier": [info signingIdentifier] ?: (id)[NSNull null],
            @"developerName": [info developerName] ?: (id)[NSNull null],
            @"teamIdentifier": [info teamIdentifier] ?: (id)[NSNull null]
        }];
    }
    NSData *json = [NSJSONSerialization dataWithJSONObject:rows options:NSJSONWritingPrettyPrinted error:nil];
    if (!json) return 1;
    fwrite(json.bytes, 1, json.length, stdout);
    putchar('\n');
    return 0;
}}
