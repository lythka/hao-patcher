// survey: which selectors of the target macOS Metal protocols do the classes of the legacy Metal stack not implement?
// usage: survey METAL_DYLIB DRIVER_BUNDLE PROTOCOLS_JSON OUT_JSON [SHIM_DYLIB]
// Loads the legacy Metal (wrapper + MetalOld) and the GPU driver bundle, optionally the shim, then for every ObjC class
// of the two images takes the Metal protocols it adopts (by name), closes them over the TARGET protocol inheritance
// (PROTOCOLS_JSON, from parse-protocols.py on the target's Metal) and checks class_respondsToSelector for every method.
#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#include <dlfcn.h>
#include <stdio.h>

static void collectAdopted(Class cls, NSMutableSet *names) {
    for (Class c = cls; c; c = class_getSuperclass(c)) {
        unsigned n = 0;
        Protocol *__unsafe_unretained *list = class_copyProtocolList(c, &n);
        for (unsigned i = 0; i < n; i++) [names addObject:@(protocol_getName(list[i]))];
        free(list);
    }
}

static void closeOverTarget(NSString *name, NSDictionary *protocols, NSMutableSet *closure) {
    if ([closure containsObject:name] || !protocols[name]) return;
    [closure addObject:name];
    for (NSString *parent in protocols[name][@"parents"]) closeOverTarget(parent, protocols, closure);
}

int main(int argc, char **argv) { @autoreleasepool {
    if (argc < 5) { fprintf(stderr, "usage: survey METAL DRIVER_BUNDLE PROTOCOLS_JSON OUT_JSON [SHIM]\n"); return 2; }
    if (!dlopen(argv[1], RTLD_NOW | RTLD_GLOBAL)) { fprintf(stderr, "dlopen Metal: %s\n", dlerror()); return 1; }
    NSBundle *bundle = [NSBundle bundleWithPath:@(argv[2])];
    NSError *err = nil;
    if (![bundle loadAndReturnError:&err]) { fprintf(stderr, "bundle: %s\n", err.description.UTF8String); return 1; }
    if (argc > 5 && !dlopen(argv[5], RTLD_NOW | RTLD_GLOBAL)) { fprintf(stderr, "dlopen shim: %s\n", dlerror()); return 1; }
    NSData *data = [NSData dataWithContentsOfFile:@(argv[3])];
    NSDictionary *target = [NSJSONSerialization JSONObjectWithData:data options:0 error:&err];
    if (!target) { fprintf(stderr, "protocols: %s\n", err.description.UTF8String); return 1; }
    NSDictionary *protocols = target[@"protocols"];
    NSDictionary *targetClasses = target[@"classes"];

    NSMutableArray *images = [NSMutableArray array];
    Dl_info info;
    // MetalOld is what the wrapper re-exports: find its image through a class it defines
    Class dev = objc_getClass("_MTLDevice");
    if (dev && dladdr((__bridge void *)dev, &info)) [images addObject:@(info.dli_fname)];
    [images addObject:bundle.executablePath];

    NSMutableDictionary *classes = [NSMutableDictionary dictionary];
    NSMutableDictionary *missingBySelector = [NSMutableDictionary dictionary];
    for (NSString *image in images) {
        unsigned n = 0;
        const char **names = objc_copyClassNamesForImage(image.UTF8String, &n);
        for (unsigned i = 0; i < n; i++) {
            Class cls = objc_getClass(names[i]);
            if (!cls) continue;
            NSMutableSet *adopted = [NSMutableSet set];
            collectAdopted(cls, adopted);
            NSMutableSet *closure = [NSMutableSet set];
            for (NSString *p in adopted) closeOverTarget(p, protocols, closure);
            if (!closure.count) continue;
            NSMutableArray *chain = [NSMutableArray array];
            for (Class c = cls; c; c = class_getSuperclass(c)) [chain addObject:@(class_getName(c))];
            NSMutableArray *missing = [NSMutableArray array];
            for (NSString *proto in [closure.allObjects sortedArrayUsingSelector:@selector(compare:)]) {
                for (NSArray *m in protocols[proto][@"methods"]) {
                    BOOL classMethod = [m[0] isEqualToString:@"+"];
                    SEL sel = sel_registerName([m[1] UTF8String]);
                    Class target = classMethod ? object_getClass(cls) : cls;
                    if (class_respondsToSelector(target, sel)) continue;
                    NSString *key = [NSString stringWithFormat:@"%@%@", m[0], m[1]];
                    [missing addObject:@{@"protocol": proto, @"selector": key, @"required": m[2]}];
                    NSMutableDictionary *entry = missingBySelector[key];
                    if (!entry) missingBySelector[key] = entry = [@{@"protocols": [NSMutableSet set], @"classes": [NSMutableSet set], @"required": m[2]} mutableCopy];
                    [entry[@"protocols"] addObject:proto];
                    [entry[@"classes"] addObject:@(names[i])];
                }
            }
            classes[@(names[i])] = @{@"image": image.lastPathComponent, @"chain": chain,
                                     @"adopted": [adopted.allObjects sortedArrayUsingSelector:@selector(compare:)],
                                     @"target_protocols": [closure.allObjects sortedArrayUsingSelector:@selector(compare:)],
                                     @"missing": missing};
        }
        free(names);
    }
    NSMutableDictionary *flat = [NSMutableDictionary dictionary];
    for (NSString *key in missingBySelector) {
        NSDictionary *e = missingBySelector[key];
        flat[key] = @{@"protocols": [[e[@"protocols"] allObjects] sortedArrayUsingSelector:@selector(compare:)],
                      @"classes": [[e[@"classes"] allObjects] sortedArrayUsingSelector:@selector(compare:)],
                      @"required": e[@"required"]};
    }
    // the target's concrete classes that exist on this stack by name: methods they do not answer
    NSMutableDictionary *classMissing = [NSMutableDictionary dictionary];
    for (NSString *name in targetClasses) {
        Class cls = objc_getClass(name.UTF8String);
        if (!cls || !dladdr((__bridge void *)cls, &info)) continue;
        NSString *image = @(info.dli_fname);
        if (![images containsObject:image]) continue;
        NSMutableArray *missing = [NSMutableArray array];
        for (NSArray *m in targetClasses[name][@"methods"]) {
            BOOL classMethod = [m[0] isEqualToString:@"+"];
            SEL sel = sel_registerName([m[1] UTF8String]);
            if (class_respondsToSelector(classMethod ? object_getClass(cls) : cls, sel)) continue;
            [missing addObject:[NSString stringWithFormat:@"%@%@", m[0], m[1]]];
        }
        if (missing.count) classMissing[name] = @{@"image": image.lastPathComponent, @"missing": missing};
    }
    NSMutableArray *absent = [NSMutableArray array];
    for (NSString *name in targetClasses) if (!objc_getClass(name.UTF8String)) [absent addObject:name];
    // a missing selector that some other class of the stack answers is a name shared across protocols: a call site
    // needs its receiver known before it counts. Selectors no class answers crash whatever the receiver.
    NSMutableSet *allMissing = [NSMutableSet setWithArray:flat.allKeys];
    for (NSDictionary *c in classMissing.allValues) [allMissing addObjectsFromArray:c[@"missing"]];
    NSMutableDictionary *elsewhere = [NSMutableDictionary dictionary];
    for (NSString *image in images) {
        unsigned n = 0;
        const char **names = objc_copyClassNamesForImage(image.UTF8String, &n);
        for (NSString *key in allMissing) {
            BOOL classMethod = [key hasPrefix:@"+"];
            SEL sel = sel_registerName([key substringFromIndex:1].UTF8String);
            for (unsigned i = 0; i < n; i++) {
                Class cls = objc_getClass(names[i]);
                if (!cls) continue;
                if (class_respondsToSelector(classMethod ? object_getClass(cls) : cls, sel)) {
                    NSMutableArray *list = elsewhere[key];
                    if (!list) elsewhere[key] = list = [NSMutableArray array];
                    [list addObject:@(names[i])];
                }
            }
        }
        free(names);
    }
    NSDictionary *result = @{@"images": images, @"classes": classes, @"missing_selectors": flat, @"implemented_elsewhere": elsewhere,
                             @"class_missing": classMissing, @"absent_classes": [absent sortedArrayUsingSelector:@selector(compare:)]};
    NSData *json = [NSJSONSerialization dataWithJSONObject:result options:NSJSONWritingPrettyPrinted | NSJSONWritingSortedKeys error:&err];
    if (!json || ![json writeToFile:@(argv[4]) atomically:YES]) { fprintf(stderr, "write: %s\n", err.description.UTF8String); return 1; }
    printf("classes surveyed %lu, missing protocol selectors %lu; named classes with missing methods %lu, absent classes %lu\n",
           (unsigned long)classes.count, (unsigned long)flat.count, (unsigned long)classMissing.count, (unsigned long)absent.count);
    return 0;
} }
